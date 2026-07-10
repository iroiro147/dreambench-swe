"""Synthetic DreamBench-SWE task sequence generator.

The fixtures generated here are deliberately small and deterministic. They are
for exercising the DreamForge memory-maintenance protocol, not for measuring
real-world coding performance yet.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Union


class SequenceType(str, Enum):
    CONVENTION_LEARNING = "convention-learning"
    GENERATED_FILES = "generated-files"
    STALE_ARCHITECTURE = "stale-architecture"
    REVIEWER_PREFERENCE = "reviewer-preference"
    FLAKY_TEST = "flaky-test"


@dataclass(frozen=True)
class SyntheticTask:
    id: str
    sequence_id: str
    seq_type: SequenceType
    session_index: int
    prompt: str
    injected_memory_event: Dict[str, Any]
    expected_behavior: str
    oracle_check: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["seq_type"] = self.seq_type.value
        return payload


@dataclass(frozen=True)
class TaskSequence:
    id: str
    seq_type: SequenceType
    sequence_index: int
    tasks: List[SyntheticTask]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fixture_version": "dreambench-swe-v0",
            "sequence": {
                "id": self.id,
                "seq_type": self.seq_type.value,
                "sequence_index": self.sequence_index,
            },
            "tasks": [task.to_dict() for task in self.tasks],
        }


def _task(
    sequence_id: str,
    seq_type: SequenceType,
    session_index: int,
    prompt: str,
    injected_memory_event: Dict[str, Any],
    expected_behavior: str,
    oracle_check: Dict[str, Any],
) -> SyntheticTask:
    return SyntheticTask(
        id=f"{sequence_id}-s{session_index:02d}",
        sequence_id=sequence_id,
        seq_type=seq_type,
        session_index=session_index,
        prompt=prompt.strip(),
        injected_memory_event=injected_memory_event,
        expected_behavior=expected_behavior.strip(),
        oracle_check=oracle_check,
    )


def _oracle(
    must_do: Iterable[str],
    must_avoid: Iterable[str],
    evidence_required: Iterable[str],
) -> Dict[str, Any]:
    return {
        "type": "synthetic_binary_checklist",
        "must_do": list(must_do),
        "must_avoid": list(must_avoid),
        "evidence_required": list(evidence_required),
    }


def _event(
    event_type: str,
    memory_type: str,
    content: str,
    source_session_index: int,
    confidence: float,
) -> Dict[str, Any]:
    return {
        "event_type": event_type,
        "memory_type": memory_type,
        "content": content,
        "source_session_index": source_session_index,
        "confidence": confidence,
    }


def _convention_learning(sequence_id: str, variant: int) -> TaskSequence:
    package = f"ledger_{variant}"
    tasks = [
        _task(
            sequence_id,
            SequenceType.CONVENTION_LEARNING,
            1,
            f"""
            Session 1: In repo synth-{package}, add a parser helper for
            invoice IDs. Existing modules use dataclasses with explicit
            to_dict methods and stdlib-only imports. Preserve that convention.
            """,
            _event(
                "observation",
                "procedural",
                "Project code favors dataclasses plus explicit to_dict serialization.",
                1,
                0.80,
            ),
            "Create or update code using dataclasses and explicit serialization, then store the convention with provenance.",
            _oracle(
                ["uses dataclass-style structure", "records convention memory"],
                ["introduces pydantic or third-party dependency", "stores ungrounded convention"],
                ["session prompt"],
            ),
        ),
        _task(
            sequence_id,
            SequenceType.CONVENTION_LEARNING,
            2,
            f"""
            Session 2: Add a matching serializer for shipment IDs in
            synth-{package}. The previous session's convention memory is
            available.
            """,
            _event(
                "memory_retrieval",
                "procedural",
                "Retrieve dataclass plus explicit to_dict convention from session 1.",
                1,
                0.82,
            ),
            "Reuse the learned convention without re-deriving it from scratch.",
            _oracle(
                ["applies session 1 convention", "keeps stdlib-only implementation"],
                ["uses a new serialization framework"],
                ["retrieved memory id", "session 1 provenance"],
            ),
        ),
        _task(
            sequence_id,
            SequenceType.CONVENTION_LEARNING,
            3,
            f"""
            Session 3: A maintainer says only public DTOs need explicit
            to_dict methods; internal helpers should use plain dataclasses.
            Update the memory policy for synth-{package}.
            """,
            _event(
                "human-feedback",
                "human_feedback",
                "Reviewer narrows the serialization convention to public DTOs.",
                3,
                0.95,
            ),
            "Write a human-feedback memory that narrows, rather than erases, the earlier convention.",
            _oracle(
                ["records narrowed scope", "links feedback to prior convention"],
                ["deletes raw session 1 evidence", "keeps overbroad convention active without qualification"],
                ["human feedback event", "prior memory id"],
            ),
        ),
        _task(
            sequence_id,
            SequenceType.CONVENTION_LEARNING,
            4,
            f"""
            Session 4: Add an internal cache key dataclass in synth-{package}.
            A stale memory says all dataclasses need explicit to_dict methods.
            """,
            _event(
                "contradiction",
                "contradiction",
                "Old broad convention conflicts with newer human feedback that limits to_dict to public DTOs.",
                4,
                0.90,
            ),
            "Suppress the overbroad memory, apply the narrowed convention, and mark the contradiction candidate.",
            _oracle(
                ["does not add to_dict to internal cache key", "marks broad memory as contradicted or superseded"],
                ["blindly follows stale broad memory"],
                ["session 3 feedback", "contradiction event"],
            ),
        ),
    ]
    return TaskSequence(sequence_id, SequenceType.CONVENTION_LEARNING, variant, tasks)


def _generated_files(sequence_id: str, variant: int) -> TaskSequence:
    module = f"schema_{variant}"
    tasks = [
        _task(
            sequence_id,
            SequenceType.GENERATED_FILES,
            1,
            f"""
            Session 1: A failing check points at generated/{module}.py, whose
            header says "generated by tools/build_schema.py". Fix the source of
            truth and regenerate the file.
            """,
            _event(
                "observation",
                "constraint",
                "generated/*.py files are derived artifacts; edit tools/build_schema.py instead.",
                1,
                0.88,
            ),
            "Avoid direct edits to generated output; update source generator and record the constraint.",
            _oracle(
                ["edits source generator", "regenerates generated file", "records generated-file constraint"],
                ["manual edit inside generated file as permanent fix"],
                ["generated file header", "generator path"],
            ),
        ),
        _task(
            sequence_id,
            SequenceType.GENERATED_FILES,
            2,
            f"""
            Session 2: generated/{module}.py again has a wrong enum value after
            a product change. The generated-file memory is available.
            """,
            _event(
                "memory_retrieval",
                "constraint",
                "Generated outputs should not be hand-edited.",
                1,
                0.88,
            ),
            "Use the generator workflow immediately and preserve provenance.",
            _oracle(
                ["starts from generator", "mentions generated-file provenance"],
                ["patches generated output only"],
                ["retrieved memory", "file header"],
            ),
        ),
        _task(
            sequence_id,
            SequenceType.GENERATED_FILES,
            3,
            f"""
            Session 3: A maintainer intentionally hand-edits
            generated/{module}.py during an emergency hotfix and labels it
            temporary until the generator is repaired.
            """,
            _event(
                "human-feedback",
                "human_feedback",
                "Temporary hotfix permits a generated-file edit but only until the generator is fixed.",
                3,
                0.93,
            ),
            "Record a time-bounded exception with high staleness risk, not a new permanent convention.",
            _oracle(
                ["marks exception as temporary", "keeps generator rule active"],
                ["turns hotfix into permanent permission"],
                ["human feedback event"],
            ),
        ),
        _task(
            sequence_id,
            SequenceType.GENERATED_FILES,
            4,
            f"""
            Session 4: The emergency has passed. Fix another generated enum in
            generated/{module}.py. A memory of the temporary hotfix is present.
            """,
            _event(
                "staleness",
                "constraint",
                "Temporary generated-file hotfix memory is now stale for normal work.",
                4,
                0.87,
            ),
            "Suppress the stale exception and return to the generator-first rule.",
            _oracle(
                ["marks hotfix exception stale", "uses generator-first repair"],
                ["reuses stale emergency exception"],
                ["staleness event", "original generated-file rule"],
            ),
        ),
    ]
    return TaskSequence(sequence_id, SequenceType.GENERATED_FILES, variant, tasks)


def _stale_architecture(sequence_id: str, variant: int) -> TaskSequence:
    service = f"notify_{variant}"
    tasks = [
        _task(
            sequence_id,
            SequenceType.STALE_ARCHITECTURE,
            1,
            f"""
            Session 1: Add a retry path to {service}. The current architecture
            routes jobs through QueueClient and stores worker config in
            config/workers.json.
            """,
            _event(
                "observation",
                "semantic_project",
                "Retry jobs for this service currently route through QueueClient.",
                1,
                0.78,
            ),
            "Record the architecture fact with commit and file provenance.",
            _oracle(
                ["uses QueueClient", "records architecture memory"],
                ["claims architecture is permanent"],
                ["current files", "session prompt"],
            ),
        ),
        _task(
            sequence_id,
            SequenceType.STALE_ARCHITECTURE,
            2,
            f"""
            Session 2: The repo migrates {service} from QueueClient to
            EventBusClient. Update the retry path accordingly.
            """,
            _event(
                "contradiction",
                "contradiction",
                "New EventBusClient architecture conflicts with QueueClient memory.",
                2,
                0.91,
            ),
            "Create a superseding architecture memory and mark the QueueClient memory superseded.",
            _oracle(
                ["uses EventBusClient", "supersedes QueueClient memory"],
                ["keeps both memories active without conflict marker"],
                ["migration prompt", "prior memory id"],
            ),
        ),
        _task(
            sequence_id,
            SequenceType.STALE_ARCHITECTURE,
            3,
            f"""
            Session 3: Fix a timeout in {service}. Retrieval returns both the
            old QueueClient memory and the newer EventBusClient memory.
            """,
            _event(
                "staleness",
                "semantic_project",
                "QueueClient architecture memory is stale after EventBusClient migration.",
                3,
                0.90,
            ),
            "Gate retrieval by staleness and choose the newer architecture memory.",
            _oracle(
                ["ignores stale QueueClient memory", "uses EventBusClient"],
                ["mixes old and new clients"],
                ["supersession link", "staleness score"],
            ),
        ),
        _task(
            sequence_id,
            SequenceType.STALE_ARCHITECTURE,
            4,
            f"""
            Session 4: A test fixture still names QueueClient in a legacy file.
            Decide whether that revives the old architecture memory.
            """,
            _event(
                "contradiction",
                "contradiction",
                "Legacy fixture reference is weak evidence and should not reactivate stale architecture.",
                4,
                0.77,
            ),
            "Treat the fixture as scoped legacy evidence and keep the stale architecture suppressed.",
            _oracle(
                ["scopes QueueClient reference to fixture", "does not reactivate stale architecture"],
                ["declares migration reversed from fixture alone"],
                ["fixture path", "new architecture memory"],
            ),
        ),
    ]
    return TaskSequence(sequence_id, SequenceType.STALE_ARCHITECTURE, variant, tasks)


def _reviewer_preference(sequence_id: str, variant: int) -> TaskSequence:
    reviewer = f"reviewer_{variant}"
    tasks = [
        _task(
            sequence_id,
            SequenceType.REVIEWER_PREFERENCE,
            1,
            f"""
            Session 1: Implement a narrow validation fix. {reviewer} comments:
            "Please avoid broad drive-by refactors in bug-fix PRs."
            """,
            _event(
                "human-feedback",
                "human_feedback",
                "Reviewer prefers narrow bug-fix diffs without drive-by refactors.",
                1,
                0.96,
            ),
            "Record the reviewer preference with task scope and avoid unrelated edits.",
            _oracle(
                ["keeps diff narrow", "records human-feedback memory"],
                ["performs unrelated cleanup"],
                ["reviewer comment"],
            ),
        ),
        _task(
            sequence_id,
            SequenceType.REVIEWER_PREFERENCE,
            2,
            f"""
            Session 2: Fix another validation bug. The same reviewer preference
            memory is available.
            """,
            _event(
                "memory_retrieval",
                "human_feedback",
                "Narrow bug-fix diffs are preferred by this reviewer.",
                1,
                0.94,
            ),
            "Apply the preference by limiting edits and mentioning scoped verification.",
            _oracle(
                ["limits changes to bug fix", "uses preference memory"],
                ["renames unrelated symbols"],
                ["retrieved human-feedback memory"],
            ),
        ),
        _task(
            sequence_id,
            SequenceType.REVIEWER_PREFERENCE,
            3,
            f"""
            Session 3: {reviewer} now asks for a small adjacent cleanup because
            the old helper makes the bug fix hard to test.
            """,
            _event(
                "contradiction",
                "human_feedback",
                "Reviewer permits adjacent cleanup when it directly enables the bug fix test.",
                3,
                0.92,
            ),
            "Narrow the preference rather than treating all cleanup as forbidden.",
            _oracle(
                ["records conditional exception", "links exception to reviewer feedback"],
                ["marks original preference deleted", "does broad unrelated cleanup"],
                ["session 3 feedback", "session 1 preference"],
            ),
        ),
        _task(
            sequence_id,
            SequenceType.REVIEWER_PREFERENCE,
            4,
            f"""
            Session 4: A cosmetic formatting cleanup is suggested while fixing a
            one-line validation bug. Decide whether reviewer memory permits it.
            """,
            _event(
                "contradiction",
                "constraint",
                "Conditional cleanup exception does not apply to cosmetic formatting unrelated to the bug.",
                4,
                0.86,
            ),
            "Reject or defer unrelated cosmetic cleanup while preserving the conditional exception.",
            _oracle(
                ["keeps cosmetic cleanup out of bug fix", "uses conditional preference correctly"],
                ["overgeneralizes cleanup exception"],
                ["session 3 exception", "session 4 task prompt"],
            ),
        ),
    ]
    return TaskSequence(sequence_id, SequenceType.REVIEWER_PREFERENCE, variant, tasks)


def _flaky_test(sequence_id: str, variant: int) -> TaskSequence:
    test_name = f"test_retry_window_{variant}"
    tasks = [
        _task(
            sequence_id,
            SequenceType.FLAKY_TEST,
            1,
            f"""
            Session 1: {test_name} fails once with a timeout but passes on a
            rerun. The task is to fix a nearby retry calculation.
            """,
            _event(
                "failure",
                "failure",
                "Single timeout followed by pass is weak evidence of flakiness.",
                1,
                0.55,
            ),
            "Mark flakiness as uncertain and do not use it to skip the real retry fix.",
            _oracle(
                ["records uncertainty", "continues investigating code change"],
                ["declares test flaky with high confidence", "ignores retry calculation"],
                ["first failure output", "rerun output"],
            ),
        ),
        _task(
            sequence_id,
            SequenceType.FLAKY_TEST,
            2,
            f"""
            Session 2: {test_name} fails twice on clean main with the same
            timeout signature and passes when run serially.
            """,
            _event(
                "memory_update",
                "failure",
                "Repeated clean-main failures raise confidence that the timeout is flaky under parallelism.",
                2,
                0.82,
            ),
            "Upgrade the failure memory with stronger provenance and a scoped retrieval condition.",
            _oracle(
                ["raises flakiness confidence", "scopes to parallel timeout signature"],
                ["marks all retry tests flaky"],
                ["clean-main runs", "serial rerun"],
            ),
        ),
        _task(
            sequence_id,
            SequenceType.FLAKY_TEST,
            3,
            f"""
            Session 3: A real regression causes {test_name} to fail with an
            assertion mismatch, not a timeout.
            """,
            _event(
                "contradiction",
                "contradiction",
                "Timeout flakiness memory does not explain assertion mismatch failure.",
                3,
                0.90,
            ),
            "Do not suppress the assertion failure as flaky; mark the memory retrieval as inapplicable.",
            _oracle(
                ["distinguishes assertion mismatch from timeout", "does not hide regression"],
                ["labels assertion mismatch flaky"],
                ["failure signature", "flaky memory scope"],
            ),
        ),
        _task(
            sequence_id,
            SequenceType.FLAKY_TEST,
            4,
            f"""
            Session 4: The timeout failure is fixed by adding deterministic
            clock control. Decide what happens to the previous flaky-test memory.
            """,
            _event(
                "staleness",
                "failure",
                "Flaky timeout memory is stale after deterministic clock fix.",
                4,
                0.88,
            ),
            "Mark the flaky-test memory stale or superseded while preserving raw failure episodes.",
            _oracle(
                ["marks flakiness memory stale or superseded", "preserves raw failure provenance"],
                ["deletes raw failure episodes", "keeps stale flaky warning active"],
                ["clock-control fix", "prior failure memory"],
            ),
        ),
    ]
    return TaskSequence(sequence_id, SequenceType.FLAKY_TEST, variant, tasks)


_BUILDERS = {
    SequenceType.CONVENTION_LEARNING: _convention_learning,
    SequenceType.GENERATED_FILES: _generated_files,
    SequenceType.STALE_ARCHITECTURE: _stale_architecture,
    SequenceType.REVIEWER_PREFERENCE: _reviewer_preference,
    SequenceType.FLAKY_TEST: _flaky_test,
}


def generate_task_sequences(sequences_per_type: int = 3) -> List[TaskSequence]:
    """Generate deterministic multi-session task sequences."""
    if sequences_per_type <= 0:
        raise ValueError("sequences_per_type must be > 0")

    sequences: List[TaskSequence] = []
    for seq_type in SequenceType:
        builder = _BUILDERS[seq_type]
        for sequence_index in range(1, sequences_per_type + 1):
            sequence_id = f"{seq_type.value}-{sequence_index:02d}"
            sequences.append(builder(sequence_id, sequence_index))
    return sequences


def flatten_tasks(sequences: Iterable[TaskSequence]) -> List[SyntheticTask]:
    """Return all tasks from generated sequences in fixture order."""
    tasks: List[SyntheticTask] = []
    for sequence in sequences:
        tasks.extend(sequence.tasks)
    return tasks


def write_json_fixtures(
    output_dir: Union[str, Path] = "experiments/data/tasks",
    sequences_per_type: int = 3,
) -> List[Path]:
    """Write one JSON fixture per sequence plus a manifest.

    Returns paths in deterministic order, with the manifest last.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    sequences = generate_task_sequences(sequences_per_type=sequences_per_type)
    written: List[Path] = []
    for sequence in sequences:
        file_path = output_path / f"{sequence.id}.json"
        with file_path.open("w", encoding="utf-8") as handle:
            json.dump(sequence.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        written.append(file_path)

    manifest = {
        "fixture_version": "dreambench-swe-v0",
        "sequence_count": len(sequences),
        "task_count": len(flatten_tasks(sequences)),
        "sequence_types": [seq_type.value for seq_type in SequenceType],
        "files": [path.name for path in written],
    }
    manifest_path = output_path / "manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    written.append(manifest_path)
    return written


def main() -> None:
    paths = write_json_fixtures(sequences_per_type=3)
    print(f"Wrote {len(paths)} DreamBench-SWE fixture files:")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
