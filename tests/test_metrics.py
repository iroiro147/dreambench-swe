"""Stdlib tests for src/dream_memory/evaluation.py.

Runs with plain python3 (no pytest required):  python3 tests/test_metrics.py
"""
import os
import sys
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dream_memory import evaluation as metrics  # noqa: E402


def assert_close(actual, expected):
    assert abs(actual - expected) < 1e-12, f"expected {expected}, got {actual}"


def assert_nan(actual):
    assert math.isnan(actual), f"expected nan, got {actual}"


def test_task_success():
    assert_close(metrics.TaskSuccess([1, 0, 1, True]), 0.75)
    assert_close(metrics.TaskSuccess(3, total_sessions=4), 0.75)
    assert_nan(metrics.TaskSuccess([]))
    assert_nan(metrics.TaskSuccess(0, total_sessions=0))


def test_pass_at_1():
    assert_close(metrics.PassAt1([True, False, True]), 2.0 / 3.0)
    assert_close(metrics.PassAt1(2, executable_sessions=5), 0.4)
    assert_nan(metrics.PassAt1([], executable_sessions=0))


def test_repeated_error_rate():
    assert_close(metrics.RepeatedErrorRate(2, 8), 0.25)
    assert_close(metrics.RepeatedErrorRate([1, 1, 0], [2, 2, 0]), 0.5)
    assert_nan(metrics.RepeatedErrorRate(1, 0))


def test_stale_memory_activation_rate():
    assert_close(metrics.StaleMemoryActivationRate(3, 12), 0.25)
    assert_close(metrics.StaleMemoryActivationRate([1, 0, 2], [4, 4, 4]), 0.25)
    assert_nan(metrics.StaleMemoryActivationRate(3, 0))


def test_harmful_memory_rate():
    assert_close(metrics.HarmfulMemoryRate([1, 0, 2], [4, 3, 5]), 0.25)
    assert_nan(metrics.HarmfulMemoryRate(0, 0))


def test_useful_memory_precision():
    assert_close(metrics.UsefulMemoryPrecision([3, 2], [4, 6]), 0.5)
    assert_close(metrics.UsefulMemoryPrecision(5, 10), 0.5)
    assert_nan(metrics.UsefulMemoryPrecision(1, 0))


def test_provenance_completeness():
    assert_close(metrics.ProvenanceCompleteness(8, 10), 0.8)
    assert_close(metrics.ProvenanceCompleteness([1, 1, 0], [1, 2, 1]), 0.5)
    assert_nan(metrics.ProvenanceCompleteness(1, 0))


def test_scope_accuracy():
    assert_close(metrics.ScopeAccuracy(7, 10), 0.7)
    assert_close(metrics.ScopeAccuracy([2, 1], [2, 3]), 0.6)
    assert_nan(metrics.ScopeAccuracy(1, 0))


def test_contradiction_repair_accuracy():
    assert_close(metrics.ContradictionRepairAccuracy(6, 8), 0.75)
    assert_close(metrics.ContradictionRepairAccuracy([1, 0, 1], [1, 1, 2]), 0.5)
    assert_nan(metrics.ContradictionRepairAccuracy(2, 0))


def test_transfer_score():
    assert_close(metrics.TransferScore([1.0, 0.5, 0.25], [0.5, 0.25, 0.25]), 0.25)
    assert_close(metrics.TransferScore([0.2, -0.1, 0.5]), 0.2)
    assert_nan(metrics.TransferScore([], []))


def test_regression_after_update():
    assert_close(metrics.RegressionAfterUpdate(1, 5), 0.2)
    assert_close(metrics.RegressionAfterUpdate([1, 0], [2, 2]), 0.25)
    assert_nan(metrics.RegressionAfterUpdate(1, 0))


def test_memory_bloat():
    assert_close(metrics.MemoryBloat(120, 40), 3.0)
    assert_close(metrics.MemoryBloat([60, 40], [25, 25]), 2.0)
    assert_nan(metrics.MemoryBloat(120, 0))


def test_cost_and_latency_metrics():
    assert_close(metrics.TotalTokens(100, 25, 5), 130.0)
    assert_close(metrics.TotalLatency(10.0, 2.5, 0.5), 13.0)
    assert_close(metrics.CostPerSuccessfulTask(24.0, 6), 4.0)
    assert_nan(metrics.CostPerSuccessfulTask(24.0, 0))
    assert_close(metrics.SleepCostShare(3.0, 12.0), 0.25)
    assert_nan(metrics.SleepCostShare(3.0, 0.0))


def test_pythonic_aliases():
    assert metrics.task_success is metrics.TaskSuccess
    assert metrics.pass_at_1 is metrics.PassAt1
    assert metrics.memory_bloat is metrics.MemoryBloat


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} metric tests passed.")
    return passed == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
