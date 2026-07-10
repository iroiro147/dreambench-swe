"""Stdlib tests for src/experiments/run_bench.py.

The test drives the ``--selftest`` path in-process and guards against accidental
subprocess or network use.
"""
from __future__ import annotations

import os
import sys
import tempfile
import json
import shlex
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from experiments import run_bench  # noqa: E402
from dream_memory.slice_scorer import HEADLINE_SLICE_METRICS  # noqa: E402


def _explode(*args, **kwargs):
    raise AssertionError("selftest must not use subprocess or network")


def test_results_root_default_honors_env() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with patch.dict(os.environ, {"DREAMBENCH_RESULTS_ROOT": tmp}):
            args = run_bench._parse_args(["--selftest"])

    assert Path(args.results_root) == Path(tmp)


TEST_ONLY_PATCH = """diff --git a/tests/test_safe_evaluate.py b/tests/test_safe_evaluate.py
--- a/tests/test_safe_evaluate.py
+++ b/tests/test_safe_evaluate.py
@@ -1,7 +1,2 @@
-from exprmini import safe_evaluate
-
-
 def test_safe_evaluate_returns_result_or_default():
-    assert safe_evaluate("2 + 3 * 4") == 14
-    assert safe_evaluate("2 +", default=0) == 0
-    assert safe_evaluate("2 +") is None
+    assert True
"""

CONFIG_SELECT_PATCH = "\n".join(
    [
        "diff --git a/configly/parser.py b/configly/parser.py",
        "index 39c74f5..368c4c4 100644",
        "--- a/configly/parser.py",
        "+++ b/configly/parser.py",
        "@@ -20,6 +20,11 @@ class Config(dict):",
        "     def as_dict(self):",
        "         return {section: dict(values) for section, values in self.items()}",
        " ",
        "+    def select(self, section, default=None):",
        "+        if section in self:",
        "+            return dict(self[section])",
        "+        return default",
        "+",
        " ",
        " def parse_ini(text):",
        "     data = {}",
        "",
    ]
)

CONFIG_TEST_PATCH = """diff --git a/tests/test_config_container.py b/tests/test_config_container.py
index d78d963..5c1410c 100644
--- a/tests/test_config_container.py
+++ b/tests/test_config_container.py
@@ -7,3 +7,7 @@ def test_parse_ini_returns_dict_compatible_config():
     assert cfg.as_dict() == {"server": {"host": "localhost"}}
     assert cfg.get_path("server.host") == "localhost"
     assert cfg.get_path("server.port", "8080") == "8080"
+
+
+def test_agent_added_select_coverage():
+    assert "AGENT_TEST_SENTINEL"
"""


class PatchAgent:
    def __init__(self, patch_text: str, *, passed: bool = True) -> None:
        self.patch_text = patch_text
        self.passed = passed
        self.last_run = {}
        self.seen_task = None

    def run_task(self, task, memory_context):
        self.seen_task = dict(task)
        tokens = run_bench._token_count(task.get("instruction", "")) + run_bench._token_count(self.patch_text)
        self.last_run = {
            "agent": "patch-agent",
            "steps": [{"action": "return-patch", "outcome": "completed"}],
            "usage": {"prompt_tokens": tokens, "completion_tokens": 0, "total_tokens": tokens},
        }
        return run_bench.TaskResult(
            patch=self.patch_text,
            passed=self.passed,
            steps=1,
            tokens=tokens,
            error_type=None if self.passed else "agent_failed",
        )


class ContinuationStubAgent:
    def __init__(self) -> None:
        self.last_run = {}
        self.s2_saw_s1_marker = False

    def run_task(self, task, memory_context):
        worktree = Path(task["worktree"])
        session_index = int(task["session_index"])
        text = (worktree / "marker.txt").read_text(encoding="utf-8")
        if session_index == 1:
            patch_text = """diff --git a/marker.txt b/marker.txt
--- a/marker.txt
+++ b/marker.txt
@@ -1 +1,2 @@
 base
+s1-marker
"""
        elif session_index == 2:
            self.s2_saw_s1_marker = "s1-marker" in text
            patch_text = """diff --git a/marker.txt b/marker.txt
--- a/marker.txt
+++ b/marker.txt
@@ -1,2 +1,3 @@
 base
 s1-marker
+s2-marker
"""
        else:
            patch_text = ""
        self.last_run = {
            "agent": "continuation-stub",
            "steps": [{"action": "return-deterministic-diff", "outcome": "completed"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
        }
        return run_bench.TaskResult(patch=patch_text, passed=bool(patch_text), steps=1, tokens=1, error_type=None)


class RaisingAgent:
    def __init__(self, message: str) -> None:
        self.message = message
        self.last_run = {"agent": "raising-agent", "failure_reason": "pre-existing agent detail"}

    def run_task(self, task, memory_context):
        raise RuntimeError(self.message)


def _first_task():
    return run_bench.load_env.load_tasks()[0]


def _requires_fixture_repos() -> None:
    repos_root = run_bench.load_env.REPOS_ROOT
    required = [repos_root / name for name in ("configly", "exprmini", "todolite")]
    if not all(path.is_dir() for path in required):
        pytest.skip("fixture repositories are not included in the public artifact")


def _requires_private_scoring_assets() -> None:
    if not run_bench.load_env.ORACLES_ROOT.is_dir():
        pytest.skip("reviewer-only oracle assets are not included in the public artifact")


def _config_stale_s2_task():
    for task in run_bench.load_env.load_tasks():
        if task["seq_id"] == "config-stale" and task["session_index"] == 2:
            return task
    raise AssertionError("missing config-stale session 2 task")


def _config_select_oracle_cmd() -> str:
    script = (
        "from pathlib import Path\n"
        "from configly import parse_ini\n"
        "cfg = parse_ini('[server]\\nhost=a\\nport=80\\n')\n"
        "selected = cfg.select('server')\n"
        "assert selected == {'host': 'a', 'port': '80'}\n"
        "selected['port'] = '81'\n"
        "assert cfg.get_path('server.port') == '80'\n"
        "assert 'AGENT_TEST_SENTINEL' not in "
        "Path('tests/test_config_container.py').read_text(encoding='utf-8')\n"
    )
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"


def _run_one_real_task(tmp: str, agent: PatchAgent):
    run_dir = Path(tmp) / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_bench._execute_sequence(
        tasks=[_first_task()],
        condition="B0",
        model="stub",
        policy=run_bench.create_policy("B0"),
        agent=agent,
        run_dir=run_dir,
        seed=run_bench.DEFAULT_SEED,
        materialize=True,
        oracle_mode="real",
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-c", "diff.external=", *args], cwd=str(cwd), text=True, capture_output=True)


def _git_checked(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    proc = _git(cwd, *args)
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc


def _write_fixture_repo(root: Path) -> tuple[Path, str]:
    repo = root / "fixture"
    repo.mkdir()
    _git_checked(repo, "init", "--quiet")
    _git_checked(repo, "config", "user.email", "test@example.invalid")
    _git_checked(repo, "config", "user.name", "Harness Test")
    (repo / "marker.txt").write_text("base\n", encoding="utf-8")
    _git_checked(repo, "add", "marker.txt")
    _git_checked(repo, "commit", "--quiet", "-m", "base")
    return repo, _git_checked(repo, "rev-parse", "HEAD").stdout.strip()


def test_selftest_runs_in_process_with_stub_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        def forbidden_codex(*args, **kwargs):
            raise AssertionError("selftest must not construct CodexAgent")

        def forbidden_grid(*args, **kwargs):
            raise AssertionError("selftest must not construct GridAgent")

        with patch("subprocess.run", side_effect=_explode), patch("urllib.request.urlopen", side_effect=_explode):
            with patch.object(run_bench, "CodexAgent", forbidden_codex), patch.object(run_bench, "GridAgent", forbidden_grid):
                code = run_bench.main(["--selftest", "--limit", "2", "--results-root", tmp])

        assert code == 0
        result_dirs = sorted(Path(tmp).iterdir())
        assert len(result_dirs) == 1
        results_path = result_dirs[0] / "results.json"
        summary_path = result_dirs[0] / "summary.md"
        assert results_path.exists()
        assert summary_path.exists()

        import json

        payload = json.loads(results_path.read_text(encoding="utf-8"))
        assert payload["selftest"] is True
        assert payload["run_type"] == "selftest"
        assert payload["manifest"]["model"] == "stub"
        assert payload["counts"]["tasks"] == 2
        assert payload["counts"]["successes"] == 2
        assert payload["metrics"]["TaskSuccess"] == 1.0
        assert payload["metrics"]["Pass@1"] == 1.0
        assert payload["records"][0]["agent_metadata"]["agent"] == "stub"


def test_tests_only_or_empty_production_diff_never_counts_as_pass() -> None:
    _requires_fixture_repos()
    _requires_private_scoring_assets()

    with tempfile.TemporaryDirectory() as tmp:
        empty_payload = _run_one_real_task(tmp, PatchAgent(""))
        empty_record = empty_payload["records"][0]
        assert empty_record["final_passed"] is False
        assert empty_record["pass_at_1"] is False
        assert empty_record["score"]["empty_production_diff"] is True
        assert empty_payload["metrics"]["TaskSuccess"] == 0.0
        assert empty_payload["metrics"]["Pass@1"] == 0.0

    with tempfile.TemporaryDirectory() as tmp:
        tests_payload = _run_one_real_task(tmp, PatchAgent(TEST_ONLY_PATCH))
        tests_record = tests_payload["records"][0]
        assert tests_record["final_passed"] is False
        assert tests_record["pass_at_1"] is False
        assert tests_record["score"]["empty_production_diff"] is True
        assert tests_record["score"]["policy_rejected"] is False
        assert tests_record["score"]["error_type"] == "empty_production_diff"
        assert "tests/test_safe_evaluate.py" in tests_record["score"]["agent_diff_files"]
        assert tests_record["score"]["rejected_files"] == []
        assert tests_payload["metrics"]["TaskSuccess"] == 0.0
        assert tests_payload["metrics"]["Pass@1"] == 0.0


def test_score_agent_diff_strips_test_hunks_without_rejecting_production_fix() -> None:
    _requires_fixture_repos()

    task = _config_stale_s2_task()
    mixed_patch = CONFIG_SELECT_PATCH + CONFIG_TEST_PATCH

    with tempfile.TemporaryDirectory() as tmp:
        score = run_bench.load_env.score_agent_diff(
            repo=task["repo"],
            base_ref=task["base_ref"],
            agent_diff=mixed_patch,
            oracle_cmd=_config_select_oracle_cmd(),
            scorer_root=tmp,
        )

        assert score["passed"] is True
        assert score["error_type"] is None
        assert score["policy_rejected"] is False
        assert score["production_files"] == ["configly/parser.py"]
        assert score["rejected_files"] == []
        assert "tests/test_config_container.py" in score["stripped_paths"]
        assert "tests/test_config_container.py" not in score["production_diff"]
        assert "def select(self, section, default=None):" in score["production_diff"]
        assert "AGENT_TEST_SENTINEL" not in score["production_diff"]
        assert not Path(score["scorer_worktree"]).exists()

    with tempfile.TemporaryDirectory() as tmp:
        score = run_bench.load_env.score_agent_diff(
            repo=task["repo"],
            base_ref=task["base_ref"],
            agent_diff=CONFIG_TEST_PATCH,
            oracle_cmd=_config_select_oracle_cmd(),
            scorer_root=tmp,
        )

        assert score["passed"] is False
        assert score["error_type"] == "empty_production_diff"
        assert score["empty_production_diff"] is True
        assert score["policy_rejected"] is False
        assert score["production_diff"] == ""
        assert "tests/test_config_container.py" in score["stripped_paths"]
        assert score["rejected_files"] == []


def test_oracle_runner_shadow_paths_are_rejected() -> None:
    for path in ("pytest.py", "pytest/__main__.py", "_pytest/__init__.py", "py.py"):
        patch_text = (
            f"diff --git a/{path} b/{path}\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            f"+++ b/{path}\n"
            "@@ -0,0 +1,2 @@\n"
            "+import sys\n"
            "+sys.exit(0)\n"
        )
        diff_info = run_bench.load_env.production_diff(patch_text)
        assert diff_info["production_diff"] == ""
        assert path in diff_info["rejected_files"]
        assert diff_info["rejected_reasons"][path] == run_bench.load_env.ORACLE_RUNNER_SHADOW_REASON


def test_pytest_module_in_worktree_cannot_shadow_oracle_runner() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        (worktree / "pytest.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        oracle = tmp_path / "hidden_oracle_test.py"
        oracle.write_text(
            "def test_hidden_oracle_really_runs():\n"
            "    assert False\n",
            encoding="utf-8",
        )

        result = run_bench.load_env.run_oracle(
            worktree,
            f"{shlex.quote(sys.executable)} -m pytest -q {shlex.quote(str(oracle))}",
            timeout_seconds=30,
        )

        assert result["passed"] is False
        assert result["exit_code"] != 0
        assert "test_hidden_oracle_really_runs" in result["stdout"]


def test_agent_public_boundary_excludes_answer_key_and_nearby_secrets() -> None:
    _requires_fixture_repos()
    _requires_private_scoring_assets()

    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "secrets.env").write_text("GRID_API_KEY=do-not-copy\n", encoding="utf-8")
        agent = PatchAgent("")
        payload = _run_one_real_task(tmp, agent)

        assert agent.seen_task is not None
        seen_task = agent.seen_task
        serialized_task = json.dumps(seen_task, sort_keys=True)
        for hidden_key in run_bench.SCORER_ONLY_KEYS:
            assert hidden_key not in seen_task
            assert hidden_key not in serialized_task
        assert "oracle_id" not in seen_task
        assert "oracle_id" not in serialized_task

        worktree = Path(seen_task["worktree"])
        assert payload["manifest"]["worktree_storage"] == "tempfile-outside-results"
        assert not _is_relative_to(worktree, run_dir)
        assert not list(worktree.rglob("tasks.jsonl"))
        assert not list(worktree.rglob("secrets.env"))
        assert not (worktree / "../../../../env/tasks.jsonl").exists()
        assert not (worktree / "../../../../secrets.env").exists()

        serialized_payload = json.dumps(payload, sort_keys=True)
        for hidden_key in run_bench.SCORER_ONLY_KEYS:
            assert hidden_key not in serialized_payload


def test_baseline_read_task_receives_scalar_read_budget_without_mutating_public_task() -> None:
    source_task = {
        "seq_id": "budget-seq",
        "sequence_id": "budget-seq",
        "seq_type": "memory-trap",
        "session_index": 3,
        "instruction": "Use the applicable reviewer marker.",
        "visible_files": ["marker.txt"],
        "_sequence_record": {
            "validation_metadata": {
                "read_budget_event_target": 2,
                "decisive_oracle_literals": ["SECRET-never-agent-visible"],
            },
        },
    }

    public_task = run_bench._public_task(source_task)
    baseline_task = run_bench._baseline_read_task(public_task, source_task)

    assert "read_budget_event_target" not in public_task
    assert baseline_task["read_budget_event_target"] == 2
    assert "decisive_oracle_literals" not in baseline_task
    assert "validation_metadata" not in baseline_task


def test_loaded_sequence_preserves_scalar_read_budget_for_baseline_only() -> None:
    record = {
        "seq_id": "loaded-budget-seq",
        "seq_type": "memory-trap",
        "repo": "exprmini",
        "initial_commit": "seed",
        "sessions": [
            {
                "session_index": 1,
                "instruction": "Warm up.",
                "visible_files": ["exprmini/operators.py"],
                "oracle_id": "loaded-budget-seq-s1",
                "oracle_cmd": "true",
            },
            {
                "session_index": 2,
                "instruction": "Warm up again.",
                "visible_files": ["exprmini/operators.py"],
                "oracle_id": "loaded-budget-seq-s2",
                "oracle_cmd": "true",
            },
            {
                "session_index": 3,
                "instruction": "Use exactly one admitted memory.",
                "visible_files": ["exprmini/operators.py"],
                "oracle_id": "loaded-budget-seq-s3",
                "oracle_cmd": "true",
            },
        ],
        "events": [],
        "oracle_labels": [],
        "validation_metadata": {
            "read_budget_event_target": 1,
            "decisive_oracle_literals": ["SECRET-never-agent-visible"],
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sequences.jsonl"
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        loaded = run_bench.load_env.load_sequence_records(path)

    session_three = [
        task for task in run_bench._ordered_run_items(loaded) if task["session_index"] == 3
    ][0]
    public_task = run_bench._public_task(session_three)
    baseline_task = run_bench._baseline_read_task(public_task, session_three)

    assert "validation_metadata" not in public_task
    assert "decisive_oracle_literals" not in json.dumps(public_task, sort_keys=True)
    assert "validation_metadata" not in baseline_task
    assert baseline_task["read_budget_event_target"] == 1


def test_pre_run_scrub_removes_stale_hidden_temp_logs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        leak_log = root / "expr-convention-validate.abcd" / "repo" / "logs" / "codex" / "b2b-seq.log"
        leak_log.parent.mkdir(parents=True)
        leak_log.write_text(
            "Exact reviewer export dialect contract: export must write EXPORT-vQ7M2-L9Z\n"
            "<REVIEWER_ONLY_ORACLES>/todo-reviewer-export-dialect/s3_test.py\n",
            encoding="utf-8",
        )
        benign_log = root / "expr-convention-validate.abcd" / "repo" / "logs" / "codex" / "benign.log"
        benign_log.write_text("ordinary status line\n", encoding="utf-8")

        old_gettempdir = run_bench.tempfile.gettempdir
        old_tmpdir = os.environ.get("TMPDIR")
        run_bench.tempfile.gettempdir = lambda: str(root)
        os.environ["TMPDIR"] = str(root)
        try:
            result = run_bench._scrub_agent_readable_hidden_logs()
        finally:
            run_bench.tempfile.gettempdir = old_gettempdir
            if old_tmpdir is None:
                os.environ.pop("TMPDIR", None)
            else:
                os.environ["TMPDIR"] = old_tmpdir

        assert os.path.realpath(leak_log) in {os.path.realpath(path) for path in result["removed"]}
        assert not leak_log.exists()
        assert benign_log.exists()


def test_task_exception_record_carries_error_detail() -> None:
    detail = "GRID request failed: container connection refused on 127.0.0.1"
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = run_bench._execute_sequence(
            tasks=[_first_task()],
            condition="B0",
            model="grid:glm-latest",
            policy=run_bench.create_policy("B0"),
            agent=RaisingAgent(detail),
            run_dir=run_dir,
            seed=run_bench.DEFAULT_SEED,
            materialize=False,
            oracle_mode="stub",
        )

    record = payload["records"][0]
    assert record["error_type"] == "task_exception:RuntimeError"
    assert record["error_detail"] == f"RuntimeError: {detail}"
    assert len(record["error_detail"]) <= 500
    assert record["agent_result"]["error_detail"] == record["error_detail"]
    assert record["score"]["error_detail"] == record["error_detail"]
    assert record["oracle"]["stderr"] == record["error_detail"]


def test_sequence_continuation_starts_s2_from_s1_end_commit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        fixture_repo, initial_commit = _write_fixture_repo(tmp_path)
        sequence = {
            "seq_id": "continuation-smoke",
            "seq_type": "memory-trap",
            "repo": str(fixture_repo),
            "initial_commit": initial_commit,
            "sessions": [
                {
                    "session_index": 1,
                    "instruction": "Add the S1 marker.",
                    "visible_files": ["marker.txt"],
                    "oracle_id": "continuation-smoke-s1",
                    "oracle_cmd": (
                        "python3 -c \"from pathlib import Path; "
                        "assert Path('marker.txt').read_text() == 'base\\ns1-marker\\n'\""
                    ),
                },
                {
                    "session_index": 2,
                    "instruction": "Add the S2 marker.",
                    "visible_files": ["marker.txt"],
                    "oracle_id": "continuation-smoke-s2",
                    "oracle_cmd": (
                        "python3 -c \"from pathlib import Path; "
                        "assert Path('marker.txt').read_text() == 'base\\ns1-marker\\ns2-marker\\n'\""
                    ),
                },
            ],
            "events": [],
            "oracle_labels": [],
        }
        sequence_path = tmp_path / "sequence.json"
        sequence_path.write_text(json.dumps(sequence), encoding="utf-8")
        sequences = run_bench.load_env.load_sequence_records(sequence_path)
        assert sequences[0]["sessions"][0]["oracle_id"] == "continuation-smoke-s1"
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        agent = ContinuationStubAgent()

        payload = run_bench._execute_sequence(
            tasks=sequences,
            condition="B0",
            model="stub",
            policy=run_bench.create_policy("B0"),
            agent=agent,
            run_dir=run_dir,
            seed=run_bench.DEFAULT_SEED,
            materialize=True,
            oracle_mode="real",
        )

        assert agent.s2_saw_s1_marker is True
        assert len(payload["records"]) == 2
        s1, s2 = payload["records"]
        assert s1["continuation_advanced"] is True
        assert s1["end_commit"] != s1["start_commit"]
        assert s2["start_commit"] == s1["end_commit"]
        assert s2["previous_session_end_commit"] == s1["end_commit"]
        assert s2["started_from_previous_session"] is True
        assert s2["forbidden_fresh_base_ref_used"] is False
        assert s2["continuation_advanced"] is True
        assert payload["manifest"]["sequence_mode"] is True
        assert payload["manifest"]["task_order"] == ["continuation-smoke-s01", "continuation-smoke-s02"]
        assert payload["slice_report"]["details"][1]["continuation_audit"]["start_commit"] == s2["start_commit"]


def test_materialized_sequence_cleans_temp_worktrees_without_breaking_continuation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        fixture_repo, initial_commit = _write_fixture_repo(tmp_path)
        sequence = {
            "seq_id": "cleanup-smoke",
            "seq_type": "memory-trap",
            "repo": str(fixture_repo),
            "initial_commit": initial_commit,
            "sessions": [
                {
                    "session_index": 1,
                    "instruction": "Add the S1 marker.",
                    "visible_files": ["marker.txt"],
                    "oracle_id": "cleanup-smoke-s1",
                    "oracle_cmd": (
                        f"{shlex.quote(sys.executable)} -c "
                        "\"from pathlib import Path; "
                        "assert Path('marker.txt').read_text() == 'base\\ns1-marker\\n'\""
                    ),
                },
                {
                    "session_index": 2,
                    "instruction": "Add the S2 marker.",
                    "visible_files": ["marker.txt"],
                    "oracle_id": "cleanup-smoke-s2",
                    "oracle_cmd": (
                        f"{shlex.quote(sys.executable)} -c "
                        "\"from pathlib import Path; "
                        "assert Path('marker.txt').read_text() == 'base\\ns1-marker\\ns2-marker\\n'\""
                    ),
                },
            ],
            "events": [],
            "oracle_labels": [],
        }
        sequence_path = tmp_path / "sequence.json"
        sequence_path.write_text(json.dumps(sequence), encoding="utf-8")
        sequences = run_bench.load_env.load_sequence_records(sequence_path)
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        temp_root = tmp_path / "tmp"
        temp_root.mkdir()
        agent = ContinuationStubAgent()
        created_paths = []
        removed_paths = []
        max_live = {"agent": 0, "scorer": 0, "state": 0}
        original_materialize = run_bench.load_env.materialize
        original_rmtree = run_bench.shutil.rmtree

        def live_children(prefix: str) -> list[Path]:
            roots = [path for path in temp_root.iterdir() if path.is_dir() and path.name.startswith(prefix)]
            return [child for root in roots for child in root.iterdir() if child.is_dir()]

        def record_live_counts() -> None:
            max_live["agent"] = max(max_live["agent"], len(live_children("dreambench-agent-")))
            max_live["scorer"] = max(max_live["scorer"], len(live_children("dreambench-scorer-")))
            max_live["state"] = max(max_live["state"], len(live_children("dreambench-state-")))

        def tracking_materialize(*args, **kwargs):
            path = original_materialize(*args, **kwargs)
            created_paths.append(Path(path))
            record_live_counts()
            return path

        def tracking_rmtree(path, *args, **kwargs):
            removed_paths.append(Path(path))
            return original_rmtree(path, *args, **kwargs)

        old_tempdir = tempfile.tempdir
        tempfile.tempdir = str(temp_root)
        try:
            with patch.object(run_bench.load_env, "materialize", side_effect=tracking_materialize):
                with patch("shutil.rmtree", side_effect=tracking_rmtree):
                    payload = run_bench._execute_sequence(
                        tasks=sequences,
                        condition="B0",
                        model="stub",
                        policy=run_bench.create_policy("B0"),
                        agent=agent,
                        run_dir=run_dir,
                        seed=run_bench.DEFAULT_SEED,
                        materialize=True,
                        oracle_mode="real",
                    )
        finally:
            tempfile.tempdir = old_tempdir

        assert agent.s2_saw_s1_marker is True
        assert len(payload["records"]) == 2
        assert max_live == {"agent": 1, "scorer": 1, "state": 1}

        created_agent_worktrees = [path for path in created_paths if path.parent.name.startswith("dreambench-agent-")]
        created_scorer_worktrees = [path for path in created_paths if path.parent.name.startswith("dreambench-scorer-")]
        created_state_repos = [path for path in created_paths if path.parent.name.startswith("dreambench-state-")]
        assert len(created_agent_worktrees) == 2
        assert len(created_scorer_worktrees) == 2
        assert len(created_state_repos) == 1

        removed_set = set(removed_paths)
        assert all(path in removed_set for path in created_agent_worktrees)
        assert all(path in removed_set for path in created_scorer_worktrees)
        assert all(path not in removed_set for path in created_state_repos)
        assert any(path.name.startswith("dreambench-agent-") for path in removed_paths)
        assert any(path.name.startswith("dreambench-scorer-") for path in removed_paths)
        assert any(path.name.startswith("dreambench-state-") for path in removed_paths)
        assert not any(
            path.is_dir()
            for path in temp_root.iterdir()
            if path.name.startswith(("dreambench-agent-", "dreambench-scorer-", "dreambench-state-"))
        )


def test_memory_trap_dry_run_writes_non_null_gate_report_without_network() -> None:
    sequences_path = Path(__file__).resolve().parents[1] / "experiments" / "env" / "sequences.jsonl"
    sequences = run_bench.load_env.load_sequence_records(sequences_path)
    requested_conditions = {"B0", "DF"}
    s3_session_count = sum(
        1
        for sequence in sequences
        for session in sequence["sessions"]
        if int(session["session_index"]) == 3
    )
    s1_s2_session_count = sum(
        1
        for sequence in sequences
        for session in sequence["sessions"]
        if int(session["session_index"]) in {1, 2}
    )
    continuation_session_count = sum(
        1
        for sequence in sequences
        for session in sequence["sessions"]
        if int(session["session_index"]) > 1
    )
    with tempfile.TemporaryDirectory() as tmp:
        with patch("urllib.request.urlopen", side_effect=_explode):
            code = run_bench.main(
                [
                    "--sequence-records",
                    str(sequences_path),
                    "--conditions",
                    "B0,DF",
                    "--dry-run",
                    "--results-root",
                    tmp,
                ]
            )

        assert code == 0
        gate_reports = sorted(Path(tmp).rglob("gate_report.json"))
        assert len(gate_reports) == 1
        report = json.loads(gate_reports[0].read_text(encoding="utf-8"))

        assert report["run_type"] == "memory_trap_dry_run"
        assert report["dry_run"] is True
        assert report["sequence_count"] == len(sequences)
        assert set(report["conditions"]) == requested_conditions

        for name in HEADLINE_SLICE_METRICS:
            metric = report["hygiene_metrics"][name]
            assert metric["denominator"] > 0, name
            assert metric["value"] is not None, name

        b0 = report["conditions"]["B0"]
        assert b0["s3_task_success"]["denominator"] == s3_session_count
        assert b0["s1_s2_task_success"]["denominator"] == s1_s2_session_count
        assert b0["sleep_error_count"] == 0
        assert "b0_s3_task_success_le_0_50" in report["acceptance_gates"]
        assert report["acceptance_gates"]["no_sleep_errors"] is True
        assert report["sleep_error_count"] == 0
        assert report["acceptance_gates"]["hygiene_metrics_non_null"] is True
        assert report["acceptance_gates"]["continuation_audit_valid"] is True

        audited = report["continuation_audit"]["records"]
        assert len(audited) == continuation_session_count * len(requested_conditions)
        for item in audited:
            assert item["condition"] in requested_conditions
            assert item["sequence_id"]
            assert item["session_index"] in {2, 3}
            assert item["start_commit"]
            assert item["end_commit"]
            assert item["previous_session_end_commit"] == item["start_commit"]
            assert item["started_from_previous_session"] is True
            assert item["forbidden_fresh_base_ref_used"] is False


def test_gate_report_marks_sleep_errors_invalid() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        report = run_bench._build_gate_report(
            parent_run_id="sleep-gate-test",
            run_type="unit",
            dry_run=False,
            sequence_records_path=tmp_path / "sequences.jsonl",
            sequences=[{"seq_id": "seq-a"}],
            condition_payloads={
                "B0": {"records": [{"task": {"seq_id": "seq-a", "session_index": 3}, "final_passed": False}]},
                "DF": {
                    "records": [
                        {
                            "task": {"seq_id": "seq-a", "session_index": 3},
                            "final_passed": True,
                            "sleep_error": "RuntimeError: judge unavailable",
                        }
                    ]
                },
            },
            condition_dirs={"B0": tmp_path / "B0", "DF": tmp_path / "DF"},
            model="stub",
            judge_model="stub-judge",
            seed=1729,
        )

    assert report["sleep_error_count"] == 1
    assert report["conditions"]["B0"]["sleep_error_count"] == 0
    assert report["conditions"]["DF"]["sleep_error_count"] == 1
    assert report["acceptance_gates"]["no_sleep_errors"] is False


def _run_all() -> bool:
    test_selftest_runs_in_process_with_stub_only()
    test_tests_only_or_empty_production_diff_never_counts_as_pass()
    test_score_agent_diff_strips_test_hunks_without_rejecting_production_fix()
    test_agent_public_boundary_excludes_answer_key_and_nearby_secrets()
    test_baseline_read_task_receives_scalar_read_budget_without_mutating_public_task()
    test_pre_run_scrub_removes_stale_hidden_temp_logs()
    test_task_exception_record_carries_error_detail()
    test_sequence_continuation_starts_s2_from_s1_end_commit()
    test_materialized_sequence_cleans_temp_worktrees_without_breaking_continuation()
    test_memory_trap_dry_run_writes_non_null_gate_report_without_network()
    test_gate_report_marks_sleep_errors_invalid()
    print("test_run_bench.py: all tests passed")
    return True


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
