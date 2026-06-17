"""
Canonical benchmark types and state machine for V1 benchmark contract.

This module provides a single source of truth for benchmark types used by both
backend (Python/FastAPI) and frontend (TypeScript). The raw-score-to-label mapping is
defined here once and used throughout the application.

Score semantics (from local evaluation notebooks):
  - 1: 正确 (correct)
  - 0: 回避或不足 (partial/avoided)
  - -1: 错误 (wrong)
"""

from enum import Enum
from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel


class BenchmarkState(str, Enum):
    """Benchmark lifecycle states."""

    idle = "idle"
    starting = "starting"
    running = "running"
    stopping = "stopping"
    stopped = "stopped"
    completed = "completed"
    failed = "failed"


# Canonical score-to-label mapping - single source of truth
SCORE_LABELS: Dict[int, str] = {
    1: "正确",
    0: "回避或不足",
    -1: "错误",
}


def score_to_label(raw_score: int) -> str:
    """
    Convert raw score to human-readable status label.

    Args:
        raw_score: Integer score (-1, 0, or 1)

    Returns:
        Human-readable label in Chinese

    Raises:
        ValueError: If raw_score is not -1, 0, or 1
    """
    if raw_score not in SCORE_LABELS:
        raise ValueError(f"Invalid raw_score: {raw_score}. Must be -1, 0, or 1.")
    return SCORE_LABELS[raw_score]


class BenchmarkResultItem(BaseModel):
    """Single question result in benchmark run."""

    question_id: str
    question_type: Optional[str] = None
    question: str
    gold_answer: str
    model_answer: str
    raw_score: int
    status_label: str
    response_time_ms: int
    completed_at: str
    error_message: Optional[str] = None
    primary_mode: Optional[str] = None
    mode_answers: Dict[str, str] = {}
    mode_scores: Dict[str, int] = {}
    mode_status_labels: Dict[str, str] = {}
    mode_response_time_ms: Dict[str, int] = {}
    mode_recall_rates: Dict[str, float] = {}


class BenchmarkModeSummary(BaseModel):
    """Per-mode benchmark summary."""

    completed: int
    correct_count: int
    partial_count: int
    wrong_count: int
    accuracy_percent: float
    avg_response_time_ms: float
    avg_recall_rate: float = 0.0


class BenchmarkSummary(BaseModel):
    """Aggregated benchmark run summary."""

    total: int
    completed: int
    correct_count: int
    partial_count: int
    wrong_count: int
    accuracy_percent: float
    avg_response_time_ms: float
    avg_recall_rate: float = 0.0
    primary_mode: Optional[str] = None
    mode_summaries: Dict[str, BenchmarkModeSummary] = {}


class BenchmarkModelOption(BaseModel):
    """Model option for benchmark selection."""

    id: str
    label: str


class BenchmarkPageSnapshot(BaseModel):
    """Snapshot for frontend page state - normalized for /benchmark/status endpoint."""

    state: BenchmarkState
    run_id: Optional[str] = None
    progress_percent: float
    summary: Optional[BenchmarkSummary] = None
    recent_results: List[BenchmarkResultItem] = []
    available_models: List[BenchmarkModelOption]
    selected_model: str
    can_start: bool
    can_stop: bool
    can_reset: bool
    error_message: Optional[str] = None


# State transition rules: from_state -> allowed to_states
# Terminal states (completed/stopped/failed) allow direct start without reset
VALID_TRANSITIONS: Dict[BenchmarkState, List[BenchmarkState]] = {
    BenchmarkState.idle: [BenchmarkState.starting],
    BenchmarkState.starting: [BenchmarkState.running, BenchmarkState.failed],
    BenchmarkState.running: [
        BenchmarkState.stopping,
        BenchmarkState.completed,
        BenchmarkState.failed,
    ],
    BenchmarkState.stopping: [BenchmarkState.stopped, BenchmarkState.failed],
    BenchmarkState.stopped: [BenchmarkState.idle, BenchmarkState.starting],
    BenchmarkState.completed: [BenchmarkState.idle, BenchmarkState.starting],
    BenchmarkState.failed: [BenchmarkState.idle, BenchmarkState.starting],
}


def is_valid_transition(from_state: BenchmarkState, to_state: BenchmarkState) -> bool:
    """
    Check if state transition is valid.

    Args:
        from_state: Current benchmark state
        to_state: Desired next state

    Returns:
        True if transition is allowed, False otherwise
    """
    allowed = VALID_TRANSITIONS.get(from_state, [])
    return to_state in allowed


def assert_valid_transition(
    from_state: BenchmarkState, to_state: BenchmarkState
) -> None:
    """
    Assert that state transition is valid, raising on invalid transition.

    Args:
        from_state: Current benchmark state
        to_state: Desired next state

    Raises:
        ValueError: If transition is not allowed
    """
    if not is_valid_transition(from_state, to_state):
        raise ValueError(
            f"Invalid state transition: {from_state.value} -> {to_state.value}. "
            f"Allowed transitions from {from_state.value}: "
            f"{[s.value for s in VALID_TRANSITIONS.get(from_state, [])]}"
        )


def build_summary_from_results(
    results: List[BenchmarkResultItem], total_questions: Optional[int] = None
) -> BenchmarkSummary:
    """
    Build summary from a list of result items.

    Args:
        results: List of benchmark result items

    Returns:
        Computed summary with counts and percentages
    """
    completed = len(results)
    total = max(total_questions or completed, completed)
    correct_count = len([r for r in results if r.raw_score == 1])
    partial_count = len([r for r in results if r.raw_score == 0])
    wrong_count = len([r for r in results if r.raw_score == -1])

    accuracy_percent = (correct_count / completed * 100) if completed > 0 else 0.0

    valid_response_times = [
        r.response_time_ms for r in results if r.error_message is None
    ]
    avg_response_time_ms = (
        sum(valid_response_times) / len(valid_response_times)
        if valid_response_times
        else 0.0
    )

    valid_recalls = [
        r.mode_recall_rates.get(r.primary_mode) for r in results 
        if r.primary_mode and r.primary_mode in r.mode_recall_rates
    ]
    avg_recall_rate = (
        sum(valid_recalls) / len(valid_recalls)
        if valid_recalls
        else 0.0
    )

    mode_summaries: Dict[str, BenchmarkModeSummary] = {}
    discovered_modes = [
        mode
        for mode in dict.fromkeys(
            mode
            for result in results
            for mode in result.mode_scores.keys()
        )
    ]

    for mode in discovered_modes:
        scored_results = [r for r in results if mode in r.mode_scores]
        if not scored_results:
            continue

        mode_correct = len([r for r in scored_results if r.mode_scores[mode] == 1])
        mode_partial = len([r for r in scored_results if r.mode_scores[mode] == 0])
        mode_wrong = len([r for r in scored_results if r.mode_scores[mode] == -1])
        mode_timings = [
            r.mode_response_time_ms[mode]
            for r in scored_results
            if mode in r.mode_response_time_ms and r.mode_response_time_ms[mode] >= 0
        ]
        
        mode_recalls = [
            r.mode_recall_rates[mode]
            for r in scored_results
            if mode in r.mode_recall_rates
        ]

        mode_summaries[mode] = BenchmarkModeSummary(
            completed=len(scored_results),
            correct_count=mode_correct,
            partial_count=mode_partial,
            wrong_count=mode_wrong,
            accuracy_percent=round(
                (mode_correct / len(scored_results) * 100) if scored_results else 0.0, 2
            ),
            avg_response_time_ms=round(
                (sum(mode_timings) / len(mode_timings)) if mode_timings else 0.0, 2
            ),
            avg_recall_rate=round(
                (sum(mode_recalls) / len(mode_recalls)) if mode_recalls else 0.0, 4
            )
        )

    primary_mode = None
    if mode_summaries:
        if "graph_text_hybrid" in mode_summaries:
            primary_mode = "graph_text_hybrid"
        else:
            primary_mode = next(iter(mode_summaries))

    return BenchmarkSummary(
        total=total,
        completed=completed,
        correct_count=correct_count,
        partial_count=partial_count,
        wrong_count=wrong_count,
        accuracy_percent=round(accuracy_percent, 2),
        avg_response_time_ms=round(avg_response_time_ms, 2),
        avg_recall_rate=round(avg_recall_rate, 4),
        primary_mode=primary_mode,
        mode_summaries=mode_summaries,
    )
