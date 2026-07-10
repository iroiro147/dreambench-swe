"""Append-only trajectory logging for DreamForge wake-phase evidence."""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class Step:
    """One observed wake-agent step."""

    action: str
    observation: str = ""
    step_id: str = field(default_factory=lambda: _new_id("step"))
    timestamp: str = field(default_factory=_utcnow)
    tool_name: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None
    tool_output: Optional[str] = None
    file_diffs: List[str] = field(default_factory=list)
    memory_reads: List[str] = field(default_factory=list)
    memory_writes: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Trajectory:
    """Raw episode evidence for one task/session."""

    task_id: str
    session_id: str
    model_id: str = "unknown"
    condition_id: str = "unknown"
    seed: Optional[int] = None
    budget: Optional[Dict[str, Any]] = None
    repo_commit: Optional[str] = None
    steps: List[Step] = field(default_factory=list)
    file_diffs: List[str] = field(default_factory=list)
    memory_reads: List[str] = field(default_factory=list)
    memory_writes: List[str] = field(default_factory=list)
    final_outcome: str = "unknown"
    raw_episode: Dict[str, Any] = field(default_factory=dict)
    trajectory_id: str = field(default_factory=lambda: _new_id("traj"))
    created_at: str = field(default_factory=_utcnow)

    def record_step(self, action: str, observation: str = "", **kwargs: Any) -> Step:
        step = Step(action=action, observation=observation, **kwargs)
        self.steps.append(step)
        self.file_diffs.extend(step.file_diffs)
        self.memory_reads.extend(step.memory_reads)
        self.memory_writes.extend(step.memory_writes)
        return step

    append_step = record_step

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["steps"] = [step.to_dict() for step in self.steps]
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def raw_episode_view(self) -> Dict[str, Any]:
        """Return a consolidation-friendly raw episode without discarding the original."""
        if self.raw_episode:
            episode = dict(self.raw_episode)
        else:
            episode = {}
        episode.setdefault("trajectory_id", self.trajectory_id)
        episode.setdefault("task_id", self.task_id)
        episode.setdefault("session_id", self.session_id)
        episode.setdefault("model_id", self.model_id)
        episode.setdefault("condition_id", self.condition_id)
        episode.setdefault("repo_commit", self.repo_commit)
        episode.setdefault("actions", [step.action for step in self.steps])
        episode.setdefault("observations", [step.observation or step.tool_output or "" for step in self.steps])
        episode.setdefault("file_paths", list(self.file_diffs))
        episode.setdefault("memory_reads", list(self.memory_reads))
        episode.setdefault("memory_writes", list(self.memory_writes))
        episode.setdefault("outcome", self.final_outcome)
        return episode


class TrajectoryLogger:
    """Append-only collection of raw trajectories."""

    def __init__(self, trajectories: Optional[Iterable[Union[Trajectory, Dict[str, Any]]]] = None) -> None:
        self._trajectories: List[Trajectory] = []
        self._last_sleep_index = 0
        for trajectory in trajectories or []:
            self.append(trajectory)

    def __len__(self) -> int:
        return len(self._trajectories)

    def record(
        self,
        *,
        task_id: str,
        session_id: str,
        model_id: str = "unknown",
        condition_id: str = "unknown",
        seed: Optional[int] = None,
        budget: Optional[Dict[str, Any]] = None,
        repo_commit: Optional[str] = None,
        steps: Optional[Iterable[Union[Step, Dict[str, Any]]]] = None,
        file_diffs: Optional[List[str]] = None,
        memory_reads: Optional[List[str]] = None,
        memory_writes: Optional[List[str]] = None,
        final_outcome: str = "unknown",
        raw_episode: Optional[Dict[str, Any]] = None,
    ) -> Trajectory:
        trajectory = Trajectory(
            task_id=task_id,
            session_id=session_id,
            model_id=model_id,
            condition_id=condition_id,
            seed=seed,
            budget=budget,
            repo_commit=repo_commit,
            file_diffs=list(file_diffs or []),
            memory_reads=list(memory_reads or []),
            memory_writes=list(memory_writes or []),
            final_outcome=final_outcome,
            raw_episode=dict(raw_episode or {}),
        )
        for step in steps or []:
            if isinstance(step, Step):
                trajectory.steps.append(step)
            else:
                trajectory.steps.append(Step(**step))
        self.append(trajectory)
        return trajectory

    def append(self, trajectory: Union[Trajectory, Dict[str, Any]]) -> Trajectory:
        item = trajectory if isinstance(trajectory, Trajectory) else trajectory_from_dict(trajectory)
        self._trajectories.append(item)
        return item

    def get_all(self) -> List[Trajectory]:
        return list(self._trajectories)

    def raw_episodes(self, *, since_last_sleep: bool = False) -> List[Dict[str, Any]]:
        trajectories = self._trajectories[self._last_sleep_index:] if since_last_sleep else self._trajectories
        return [trajectory.raw_episode_view() for trajectory in trajectories]

    def mark_sleep_checkpoint(self) -> None:
        self._last_sleep_index = len(self._trajectories)

    def to_json(self) -> str:
        return json.dumps([trajectory.to_dict() for trajectory in self._trajectories], indent=2, sort_keys=True)

    def save(self, path: Union[str, Path]) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Union[str, Path]) -> "TrajectoryLogger":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data)


def trajectory_from_dict(payload: Dict[str, Any]) -> Trajectory:
    data = dict(payload)
    step_payloads = data.pop("steps", [])
    steps = [step if isinstance(step, Step) else Step(**step) for step in step_payloads]
    if "id" in data and "trajectory_id" not in data:
        data["trajectory_id"] = data.pop("id")
    trajectory = Trajectory(**data)
    trajectory.steps = steps
    return trajectory


__all__ = ["Step", "Trajectory", "TrajectoryLogger", "trajectory_from_dict"]
