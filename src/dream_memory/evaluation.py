"""DreamForge evaluation metrics.

The metric names intentionally mirror the paper and metrics document. Functions
accept simple counts or lists, use only the Python standard library, and return
``float('nan')`` when a denominator has no evaluable items. No opportunities is
undefined, not a perfect score.
"""
from __future__ import annotations

from collections.abc import Iterable
from numbers import Real
from typing import Optional, Union


NumberOrIterable = Union[Real, Iterable[Real]]


def _is_scalar(value: object) -> bool:
    return isinstance(value, Real)


def _as_list(values: NumberOrIterable) -> list[float]:
    if _is_scalar(values):
        return [float(values)]
    return [float(value) for value in values]


def _sum(values: NumberOrIterable) -> float:
    if _is_scalar(values):
        return float(values)
    return float(sum(values))


def _rate(numerator: NumberOrIterable, denominator: NumberOrIterable) -> float:
    denominator_value = _sum(denominator)
    if denominator_value == 0.0:
        return float("nan")
    return _sum(numerator) / denominator_value


def _mean(values: NumberOrIterable) -> float:
    items = _as_list(values)
    if not items:
        return float("nan")
    return sum(items) / float(len(items))


def TaskSuccess(successes: NumberOrIterable, total_sessions: Optional[Real] = None) -> float:
    """TaskSuccess = sum_i TaskSuccess_i / N."""
    if total_sessions is None:
        return _mean(successes)
    return _rate(successes, total_sessions)


def PassAt1(first_attempt_passes: NumberOrIterable, executable_sessions: Optional[Real] = None) -> float:
    """Pass@1 = sum_i Pass@1_i / N_exec."""
    if executable_sessions is None:
        return _mean(first_attempt_passes)
    return _rate(first_attempt_passes, executable_sessions)


def RepeatedErrorRate(
    repeated_error_events: NumberOrIterable,
    opportunities_to_avoid_repetition: NumberOrIterable,
) -> float:
    """RepeatedErrorRate = count(repeated_error_events) / count(opportunities_to_avoid_repetition)."""
    return _rate(repeated_error_events, opportunities_to_avoid_repetition)


def StaleMemoryActivationRate(
    stale_or_superseded_memories_used: NumberOrIterable,
    tasks_with_stale_memory_available: NumberOrIterable,
) -> float:
    """StaleMemoryActivationRate = count(stale_or_superseded_memories_used) / count(tasks_with_stale_memory_available)."""
    return _rate(stale_or_superseded_memories_used, tasks_with_stale_memory_available)


def HarmfulMemoryRate(
    harmful_memory_counts: NumberOrIterable,
    admitted_memory_counts: NumberOrIterable,
) -> float:
    """HarmfulMemoryRate = sum_i |H_i| / sum_i |R_i|."""
    return _rate(harmful_memory_counts, admitted_memory_counts)


def UsefulMemoryPrecision(
    useful_memory_counts: NumberOrIterable,
    admitted_memory_counts: NumberOrIterable,
) -> float:
    """UsefulMemoryPrecision = sum_i |U_i| / sum_i |R_i|."""
    return _rate(useful_memory_counts, admitted_memory_counts)


def ProvenanceCompleteness(
    active_memories_with_raw_episode_provenance: NumberOrIterable,
    active_memories: NumberOrIterable,
) -> float:
    """ProvenanceCompleteness = count(active memories with provenance linking to >=1 raw episode) / count(active memories)."""
    return _rate(active_memories_with_raw_episode_provenance, active_memories)


def ScopeAccuracy(
    retrieved_memories_with_matching_scope: NumberOrIterable,
    retrieved_memories: NumberOrIterable,
) -> float:
    """ScopeAccuracy = count(retrieved memories whose scope matches task repo/file/symbol scope) / count(retrieved memories)."""
    return _rate(retrieved_memories_with_matching_scope, retrieved_memories)


def ContradictionRepairAccuracy(
    correct_contradiction_repairs: NumberOrIterable,
    evaluable_contradictions: NumberOrIterable,
) -> float:
    """ContradictionRepairAccuracy = count(correct_contradiction_repairs) / count(evaluable_contradictions)."""
    return _rate(correct_contradiction_repairs, evaluable_contradictions)


def TransferScore(
    outcomes_with_prior_memory: NumberOrIterable,
    outcomes_without_prior_memory: Optional[NumberOrIterable] = None,
) -> float:
    """TransferScore = mean_j(outcome_with_prior_memory_j - outcome_without_prior_memory_j)."""
    if outcomes_without_prior_memory is None:
        return _mean(outcomes_with_prior_memory)

    paired_differences = [
        with_memory - without_memory
        for with_memory, without_memory in zip(
            _as_list(outcomes_with_prior_memory),
            _as_list(outcomes_without_prior_memory),
        )
    ]
    return _mean(paired_differences)


def RegressionAfterUpdate(
    memory_update_regressions: NumberOrIterable,
    memory_updates_with_future_dependency: NumberOrIterable,
) -> float:
    """RegressionAfterUpdate = count(memory_update_regressions) / count(memory_updates_with_future_dependency)."""
    return _rate(memory_update_regressions, memory_updates_with_future_dependency)


def MemoryBloat(total_memory_tokens: NumberOrIterable, useful_memory_tokens: NumberOrIterable) -> float:
    """MemoryBloat = total_memory_tokens / useful_memory_tokens."""
    return _rate(total_memory_tokens, useful_memory_tokens)


def TotalTokens(wake_tokens: Real, sleep_tokens: Real, judge_tokens: Real) -> float:
    """TotalTokens = WakeTokens + SleepTokens + JudgeTokens."""
    return float(wake_tokens + sleep_tokens + judge_tokens)


def TotalLatency(wake_latency: Real, sleep_latency: Real, judge_latency: Real) -> float:
    """TotalLatency = WakeLatency + SleepLatency + JudgeLatency."""
    return float(wake_latency + sleep_latency + judge_latency)


def CostPerSuccessfulTask(total_cost: Real, successful_tasks: NumberOrIterable) -> float:
    """CostPerSuccessfulTask = TotalCost / count(successful_tasks)."""
    return _rate(total_cost, successful_tasks)


def SleepCostShare(sleep_cost: Real, total_cost: Real) -> float:
    """SleepCostShare = SleepCost / TotalCost."""
    return _rate(sleep_cost, total_cost)


task_success = TaskSuccess
pass_at_1 = PassAt1
repeated_error_rate = RepeatedErrorRate
stale_memory_activation_rate = StaleMemoryActivationRate
harmful_memory_rate = HarmfulMemoryRate
useful_memory_precision = UsefulMemoryPrecision
provenance_completeness = ProvenanceCompleteness
scope_accuracy = ScopeAccuracy
contradiction_repair_accuracy = ContradictionRepairAccuracy
transfer_score = TransferScore
regression_after_update = RegressionAfterUpdate
memory_bloat = MemoryBloat
total_tokens = TotalTokens
total_latency = TotalLatency
cost_per_successful_task = CostPerSuccessfulTask
sleep_cost_share = SleepCostShare


__all__ = [
    "TaskSuccess",
    "PassAt1",
    "RepeatedErrorRate",
    "StaleMemoryActivationRate",
    "HarmfulMemoryRate",
    "UsefulMemoryPrecision",
    "ProvenanceCompleteness",
    "ScopeAccuracy",
    "ContradictionRepairAccuracy",
    "TransferScore",
    "RegressionAfterUpdate",
    "MemoryBloat",
    "TotalTokens",
    "TotalLatency",
    "CostPerSuccessfulTask",
    "SleepCostShare",
    "task_success",
    "pass_at_1",
    "repeated_error_rate",
    "stale_memory_activation_rate",
    "harmful_memory_rate",
    "useful_memory_precision",
    "provenance_completeness",
    "scope_accuracy",
    "contradiction_repair_accuracy",
    "transfer_score",
    "regression_after_update",
    "memory_bloat",
    "total_tokens",
    "total_latency",
    "cost_per_successful_task",
    "sleep_cost_share",
]
