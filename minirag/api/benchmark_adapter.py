from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from minirag.api.benchmark_types import (
    BenchmarkResultItem,
    BenchmarkState,
    SCORE_LABELS,
    score_to_label,
)
from minirag.datasource_resolver import DatasourceResolutionError, resolve_datasource

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASOURCE_ID = "local_ship_docs"
DEFAULT_DATASOURCE_BENCHMARK_SOURCE_RELATIVE_PATH = (
    Path("outputs") / "benchmark" / "query_set.csv"
)
DEFAULT_BENCHMARK_RESULT_COLUMN = "faultcase_fast_stream_plain"

_ERROR_PREFIX = "[ERROR]"
_TIMEOUT_PATTERN = re.compile(r"read timed out|timed out|timeout", re.IGNORECASE)
_SERVER_ERROR_PATTERN = re.compile(r"\b5\d\d\b.*server error", re.IGNORECASE)


def _normalize_selector(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _resolve_path(path_value: str | Path, *, repo_root: Path) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _resolve_datasource_root(
    *,
    datasource_id: Optional[str] = None,
    datasource_root: str | Path | None = None,
    repo_root: Path,
) -> Optional[Path]:
    if datasource_root is not None:
        return _resolve_path(datasource_root, repo_root=repo_root)

    selector_kwargs = {
        "datasource_id": _normalize_selector(datasource_id)
        or _normalize_selector(os.getenv("DATASOURCE_ID")),
        "datasource_root": _normalize_selector(os.getenv("DATASOURCE_ROOT")),
        "input_dir": _normalize_selector(os.getenv("INPUT_DIR")),
        "working_dir": _normalize_selector(os.getenv("WORKING_DIR")),
    }

    if not any(selector_kwargs.values()):
        selector_kwargs["datasource_id"] = DEFAULT_DATASOURCE_ID

    try:
        return resolve_datasource(repo_root=repo_root, **selector_kwargs).root
    except DatasourceResolutionError:
        return None


def resolve_ship_benchmark_source(
    *,
    datasource_id: Optional[str] = None,
    datasource_root: str | Path | None = None,
    repo_root: str | Path | None = None,
) -> Path:
    resolved_repo_root = (
        Path(repo_root).expanduser().resolve() if repo_root is not None else REPO_ROOT
    )
    resolved_datasource_root = _resolve_datasource_root(
        datasource_id=datasource_id,
        datasource_root=datasource_root,
        repo_root=resolved_repo_root,
    )

    candidate_paths: list[Path] = []
    if resolved_datasource_root is not None:
        candidate_paths.append(
            (
                resolved_datasource_root
                / DEFAULT_DATASOURCE_BENCHMARK_SOURCE_RELATIVE_PATH
            ).resolve()
        )
    for candidate in candidate_paths:
        if candidate.exists():
            return candidate

    if candidate_paths:
        return candidate_paths[0]

    return (
        resolved_repo_root
        / "datasources"
        / DEFAULT_DATASOURCE_ID
        / DEFAULT_DATASOURCE_BENCHMARK_SOURCE_RELATIVE_PATH
    ).resolve()


def _normalize_completed_at(completed_at: Optional[Any] = None) -> str:
    if isinstance(completed_at, str):
        value = completed_at.strip()
        if value:
            return value
    if isinstance(completed_at, datetime):
        timestamp = completed_at
    else:
        timestamp = datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _stringify_row_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _first_present_row_value(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = _stringify_row_value(row.get(key))
        if value:
            return value
    return ""


def _normalize_source_row(row: Mapping[str, Any]) -> dict[str, str]:
    """
    Add canonical aliases for benchmark CSV headers while preserving originals.

    The repository benchmark source currently uses title-cased headers such as
    `Question` and `Gold Answer`, while the runtime executor reads lowercase
    keys like `question` and `gold_answer`. Normalizing at load time keeps the
    rest of the benchmark pipeline consistent and prevents empty prompts from
    reaching the chat backend.
    """

    normalized = {str(key): _stringify_row_value(value) for key, value in row.items()}
    aliases = {
        "question": _first_present_row_value(row, "question", "Question"),
        "gold_answer": _first_present_row_value(
            row, "gold_answer", "Gold Answer", "answer", "Answer"
        ),
        "answer": _first_present_row_value(
            row, "answer", "Answer", "gold_answer", "Gold Answer"
        ),
        "evidence": _first_present_row_value(row, "evidence", "Evidence"),
        "question_type": _first_present_row_value(row, "question_type", "Type"),
    }
    for key, value in aliases.items():
        if value:
            normalized[key] = value
    return normalized


def _resolve_question_id(row: Mapping[str, Any]) -> str:
    explicit_question_id = _first_present_row_value(
        row,
        "question_id",
        "questionId",
        "Question ID",
        "QuestionID",
        "id",
    )
    if explicit_question_id:
        return explicit_question_id

    evidence = _first_present_row_value(row, "evidence", "Evidence")
    question = _first_present_row_value(row, "question", "Question")
    question_digest = (
        hashlib.md5(question.encode("utf-8")).hexdigest()[:8] if question else ""
    )
    if evidence:
        evidence_prefix = evidence.split("|", 1)[0].strip()
        if evidence_prefix:
            return (
                f"{evidence_prefix}::{question_digest}"
                if question_digest
                else evidence_prefix
            )

    if question:
        return f"question-{question_digest}"

    return "<unknown>"


def _parse_numeric_ms(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return max(0, int(round(float(value))))
    except (TypeError, ValueError):
        return None


def _parse_numeric_seconds(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return max(0, int(round(float(value) * 1000)))
    except (TypeError, ValueError):
        return None


def _derive_response_time_ms(
    metrics: Optional[Mapping[str, Any]] = None,
    elapsed_seconds: Optional[float] = None,
) -> int:
    metrics = metrics or {}

    for key in ("response_time_ms", "elapsed_ms", "full_ms"):
        parsed = _parse_numeric_ms(metrics.get(key))
        if parsed is not None:
            return parsed

    for key in (
        "full_s",
        "full_seconds",
        "ttft_total_s",
        "ttft_seconds",
        "elapsed_seconds",
        "ttft_generation_only_s",
    ):
        parsed = _parse_numeric_seconds(metrics.get(key))
        if parsed is not None:
            return parsed

    parsed = _parse_numeric_ms(metrics.get("retrieval_ms"))
    if parsed is not None:
        return parsed

    if elapsed_seconds is not None:
        parsed = _parse_numeric_seconds(elapsed_seconds)
        if parsed is not None:
            return parsed

    return 0


def _extract_model_answer(row: Mapping[str, Any], result_column: str) -> str:
    for key in (result_column, "model_answer", "answer_text", "response"):
        value = _stringify_row_value(row.get(key))
        if value:
            return value
    return ""


def _extract_mode_answers(row: Mapping[str, Any], provided: Optional[Mapping[str, Any]]) -> dict[str, str]:
    raw_mapping = provided if provided is not None else row.get("mode_answers")
    if not isinstance(raw_mapping, Mapping):
        return {}
    return {
        str(key): _stringify_row_value(value)
        for key, value in raw_mapping.items()
        if _stringify_row_value(value)
    }


def _extract_mode_scores(row: Mapping[str, Any], provided: Optional[Mapping[str, Any]]) -> dict[str, int]:
    raw_mapping = provided if provided is not None else row.get("mode_scores")
    if not isinstance(raw_mapping, Mapping):
        return {}

    normalized: dict[str, int] = {}
    for key, value in raw_mapping.items():
        try:
            score = int(value)
        except (TypeError, ValueError):
            continue
        if score in SCORE_LABELS:
            normalized[str(key)] = score
    return normalized


def _extract_mode_response_times_ms(
    row: Mapping[str, Any],
    provided: Optional[Mapping[str, Any]],
) -> dict[str, int]:
    raw_mapping = provided if provided is not None else row.get("mode_metrics")
    if not isinstance(raw_mapping, Mapping):
        return {}

    normalized: dict[str, int] = {}
    for key, value in raw_mapping.items():
        mode = str(key)
        if isinstance(value, Mapping):
            normalized[mode] = _derive_response_time_ms(value)
            continue
        parsed = _parse_numeric_ms(value)
        if parsed is not None:
            normalized[mode] = parsed
    return normalized


def _extract_mode_recall_rates(
    row: Mapping[str, Any],
    provided: Optional[Mapping[str, Any]],
) -> dict[str, float]:
    raw_mapping = provided if provided is not None else row.get("mode_metrics")
    if not isinstance(raw_mapping, Mapping):
        return {}

    normalized: dict[str, float] = {}
    for key, value in raw_mapping.items():
        if not isinstance(value, Mapping):
            continue
        try:
            normalized[str(key)] = round(float(value.get("recall_rate")), 4)
        except (TypeError, ValueError):
            continue
    return normalized


def _extract_raw_score(
    row: Mapping[str, Any],
    result_column: str,
    error_message: Optional[str],
    raw_score: Optional[int],
) -> int:
    if raw_score is not None:
        return raw_score

    candidate_keys = (
        "raw_score",
        "score",
        f"judge_{result_column}",
        f"{result_column}_score",
    )
    for key in candidate_keys:
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue

    if error_message:
        return -1

    question_id = _stringify_row_value(row.get("question_id")) or "<unknown>"
    raise ValueError(
        "Successful benchmark row is missing a judged score; "
        f"cannot normalize canonical result item for question_id={question_id}."
    )


def _extract_error_message(raw_output: str) -> Optional[str]:
    text = _stringify_row_value(raw_output)
    if not text:
        return None

    stripped = (
        text[len(_ERROR_PREFIX) :].strip() if text.startswith(_ERROR_PREFIX) else text
    )

    if _TIMEOUT_PATTERN.search(stripped):
        return f"Timeout: {stripped}"
    if _SERVER_ERROR_PATTERN.search(stripped):
        return f"Server error: {stripped}"
    if text.startswith(_ERROR_PREFIX):
        return f"Benchmark error: {stripped}"
    return None


def parse_source_record_ids(raw_value: Any) -> list[str]:
    value = _stringify_row_value(raw_value)
    if not value:
        return []
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        return [
            _stringify_row_value(item) for item in parsed if _stringify_row_value(item)
        ]
    return [part.strip() for part in value.split("<SEP>") if part.strip()]


def parse_evidence_source_ids(raw_value: Any) -> list[str]:
    value = _stringify_row_value(raw_value)
    if not value:
        return []

    evidence_head = value.split("|", 1)[0].strip()
    if not evidence_head:
        return []

    doc_prefix = evidence_head.split("::", 1)[0].strip() if "::" in evidence_head else ""
    seen: set[str] = set()
    source_ids: list[str] = []
    for part in evidence_head.replace(";", ",").split(","):
        candidate = _stringify_row_value(part)
        if candidate and "::" not in candidate and doc_prefix:
            candidate = f"{doc_prefix}::{candidate}"
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        source_ids.append(candidate)
    return source_ids


def parse_context_source_ids(raw_context: Any) -> list[str]:
    context = str(raw_context or "").strip()
    if not context:
        return []

    match = re.search(
        r"-----Sources-----\s*```csv\s*(.*?)\s*```",
        context,
        re.DOTALL,
    )
    if not match:
        return []

    csv_block = match.group(1).strip()
    if not csv_block:
        return []

    seen: set[str] = set()
    source_ids: list[str] = []
    for row in csv.DictReader(StringIO(csv_block)):
        for candidate in (
            _stringify_row_value(row.get("id")),
            *_extract_record_ids_from_text(row.get("content")),
        ):
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            source_ids.append(candidate)
    return source_ids


def _source_id_match_keys(value: str) -> set[str]:
    normalized = _stringify_row_value(value)
    if not normalized:
        return set()

    keys = {normalized}
    if "::" in normalized:
        keys.add(normalized.rsplit("::", 1)[-1])
    return keys


def _extract_record_ids_from_text(value: Any) -> list[str]:
    text = str(value or "")
    if not text:
        return []
    return [
        match.group(1).strip()
        for match in re.finditer(r"\[记录ID\]\s*([^\s\[]+)", text)
        if match.group(1).strip()
    ]


def compute_recall_rate(
    expected_ids: Sequence[str], retrieved_ids: Sequence[str]
) -> float:
    normalized_expected = [
        _stringify_row_value(item)
        for item in expected_ids
        if _stringify_row_value(item)
    ]
    if not normalized_expected:
        return 0.0

    retrieved_keys: set[str] = set()
    for item in retrieved_ids:
        retrieved_keys.update(_source_id_match_keys(item))

    hit_count = sum(
        1
        for item in normalized_expected
        if _source_id_match_keys(item) & retrieved_keys
    )
    return round(hit_count / len(normalized_expected) * 100.0, 4)


class ShipBenchmarkRunnerAdapter:
    """Thin adapter for the current ship-domain benchmark CSV/script flow."""

    def __init__(
        self,
        state_container: Any,
        source_csv_path: str | Path | None = None,
        result_column: str = DEFAULT_BENCHMARK_RESULT_COLUMN,
        datasource_id: Optional[str] = None,
        datasource_root: str | Path | None = None,
        repo_root: str | Path | None = None,
    ):
        self.state_container = state_container
        resolved_repo_root = (
            Path(repo_root).expanduser().resolve() if repo_root is not None else REPO_ROOT
        )
        self.source_csv_path = (
            _resolve_path(source_csv_path, repo_root=resolved_repo_root)
            if source_csv_path is not None
            else resolve_ship_benchmark_source(
                datasource_id=datasource_id,
                datasource_root=datasource_root,
                repo_root=resolved_repo_root,
            )
        )
        self.result_column = result_column

    def configure_source(
        self,
        *,
        source_csv_path: str | Path | None = None,
        datasource_id: Optional[str] = None,
        datasource_root: str | Path | None = None,
        repo_root: str | Path | None = None,
    ) -> Path:
        resolved_repo_root = (
            Path(repo_root).expanduser().resolve() if repo_root is not None else REPO_ROOT
        )
        self.source_csv_path = (
            _resolve_path(source_csv_path, repo_root=resolved_repo_root)
            if source_csv_path is not None
            else resolve_ship_benchmark_source(
                datasource_id=datasource_id,
                datasource_root=datasource_root,
                repo_root=resolved_repo_root,
            )
        )
        return self.source_csv_path

    def load_source_rows(self) -> list[dict[str, str]]:
        with self.source_csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [_normalize_source_row(row) for row in csv.DictReader(handle)]

    def normalize_result_row(
        self,
        row: Mapping[str, Any],
        *,
        raw_score: Optional[int] = None,
        metrics: Optional[Mapping[str, Any]] = None,
        mode_answers: Optional[Mapping[str, Any]] = None,
        mode_scores: Optional[Mapping[str, Any]] = None,
        mode_metrics: Optional[Mapping[str, Any]] = None,
        primary_mode: Optional[str] = None,
        completed_at: Optional[datetime] = None,
        elapsed_seconds: Optional[float] = None,
    ) -> BenchmarkResultItem:
        resolved_mode_answers = _extract_mode_answers(row, mode_answers)
        resolved_mode_scores = _extract_mode_scores(row, mode_scores)
        resolved_mode_response_times_ms = _extract_mode_response_times_ms(row, mode_metrics)
        resolved_mode_recall_rates = _extract_mode_recall_rates(row, mode_metrics)
        resolved_primary_mode = (
            _stringify_row_value(primary_mode or row.get("primary_mode")) or None
        )
        if resolved_primary_mode is None and resolved_mode_scores:
            resolved_primary_mode = (
                "graph_text_hybrid"
                if "graph_text_hybrid" in resolved_mode_scores
                else next(iter(resolved_mode_scores))
            )

        raw_output = (
            resolved_mode_answers.get(resolved_primary_mode, "")
            if resolved_primary_mode is not None
            else _extract_model_answer(row, self.result_column)
        )
        if not raw_output:
            raw_output = _extract_model_answer(row, self.result_column)
        derived_error_message = _extract_error_message(raw_output)
        explicit_error_message = _first_present_row_value(row, "error_message") or None
        error_message = derived_error_message or explicit_error_message
        resolved_raw_score = (
            resolved_mode_scores.get(resolved_primary_mode)
            if resolved_primary_mode is not None and resolved_primary_mode in resolved_mode_scores
            else None
        )
        resolved_raw_score = _extract_raw_score(
            row,
            self.result_column,
            error_message,
            raw_score if raw_score is not None else resolved_raw_score,
        )
        mode_status_labels = {
            mode: score_to_label(score) for mode, score in resolved_mode_scores.items()
        }

        return BenchmarkResultItem(
            question_id=_resolve_question_id(row),
            question_type=_first_present_row_value(
                row,
                "question_type",
                "questionType",
                "Type",
            )
            or None,
            question=_first_present_row_value(row, "question", "Question"),
            gold_answer=_first_present_row_value(
                row,
                "answer",
                "gold_answer",
                "Gold Answer",
            ),
            model_answer="" if derived_error_message else raw_output,
            raw_score=resolved_raw_score,
            status_label=score_to_label(resolved_raw_score),
            response_time_ms=_derive_response_time_ms(metrics, elapsed_seconds),
            completed_at=_normalize_completed_at(completed_at),
            error_message=error_message,
            primary_mode=resolved_primary_mode,
            mode_answers=resolved_mode_answers,
            mode_scores=resolved_mode_scores,
            mode_status_labels=mode_status_labels,
            mode_response_time_ms=resolved_mode_response_times_ms,
            mode_recall_rates=resolved_mode_recall_rates,
        )

    def record_result(
        self,
        row: Mapping[str, Any],
        *,
        question_index: int,
        on_result: Optional[Callable[[BenchmarkResultItem], None]] = None,
        raw_score: Optional[int] = None,
        metrics: Optional[Mapping[str, Any]] = None,
        mode_answers: Optional[Mapping[str, Any]] = None,
        mode_scores: Optional[Mapping[str, Any]] = None,
        mode_metrics: Optional[Mapping[str, Any]] = None,
        primary_mode: Optional[str] = None,
        completed_at: Optional[datetime] = None,
        elapsed_seconds: Optional[float] = None,
    ) -> BenchmarkResultItem:
        item = self.normalize_result_row(
            row,
            raw_score=raw_score,
            metrics=metrics,
            mode_answers=mode_answers,
            mode_scores=mode_scores,
            mode_metrics=mode_metrics,
            primary_mode=primary_mode,
            completed_at=completed_at,
            elapsed_seconds=elapsed_seconds,
        )
        self.state_container.add_result(item, question_index=question_index)
        if on_result is not None:
            on_result(item)
        return item

    async def arun_current_flow(
        self,
        run_id: str,
        executor_async: Callable[[Mapping[str, Any]], Awaitable[Any]],
        rows: Optional[Sequence[Mapping[str, Any]]] = None,
        on_result: Optional[Callable[[BenchmarkResultItem], None]] = None,
        concurrency: int = 5,
    ) -> list[BenchmarkResultItem]:
        import asyncio

        source_rows = list(rows) if rows is not None else self.load_source_rows()
        self.state_container.start(run_id, len(source_rows))
        normalized_items: list[BenchmarkResultItem] = []

        if self.state_container.should_stop():
            if getattr(self.state_container, "state", None) == BenchmarkState.stopping:
                self.state_container.finalize_stopped()
            return normalized_items

        self.state_container.transition_to_running()
        semaphore = asyncio.Semaphore(concurrency)
        progress_lock = asyncio.Lock()
        completed_count = 0
        
        async def process_one(index: int, source_row: Mapping[str, Any]) -> None:
            nonlocal completed_count
            if self.state_container.should_stop():
                return
                
            async with semaphore:
                if self.state_container.should_stop():
                    return
                    
                started = time.perf_counter()
                execution_result = await executor_async(source_row)
                elapsed_seconds = time.perf_counter() - started

                if getattr(self.state_container, "state", None) == BenchmarkState.stopping:
                    return

                if isinstance(execution_result, Mapping):
                    if execution_result.get("stop_requested"):
                        if getattr(self.state_container, "state", None) == BenchmarkState.stopping:
                            self.state_container.finalize_stopped()
                        return
                    merged_row = dict(source_row)
                    merged_row.update(execution_result)
                    raw_score = execution_result.get("raw_score")
                    metrics = execution_result.get("metrics")
                    mode_answers = execution_result.get("mode_answers")
                    mode_scores = execution_result.get("mode_scores")
                    mode_metrics = execution_result.get("mode_metrics")
                    primary_mode = execution_result.get("primary_mode")
                    completed_at = execution_result.get("completed_at")
                else:
                    merged_row = dict(source_row)
                    merged_row[self.result_column] = execution_result
                    raw_score = None
                    metrics = None
                    mode_answers = None
                    mode_scores = None
                    mode_metrics = None
                    primary_mode = None
                    completed_at = None

                async with progress_lock:
                    completed_count += 1
                    completed_index = completed_count

                item = self.record_result(
                    merged_row,
                    question_index=completed_index,
                    on_result=on_result,
                    raw_score=raw_score,
                    metrics=metrics,
                    mode_answers=mode_answers,
                    mode_scores=mode_scores,
                    mode_metrics=mode_metrics,
                    primary_mode=primary_mode,
                    completed_at=completed_at,
                    elapsed_seconds=elapsed_seconds,
                )
                normalized_items.append(item)

        tasks = [
            process_one(index, source_row) 
            for index, source_row in enumerate(source_rows, start=1)
        ]
        await asyncio.gather(*tasks)
        
        if getattr(self.state_container, "state", None) == BenchmarkState.stopping:
            self.state_container.finalize_stopped()
        elif getattr(self.state_container, "state", None) == BenchmarkState.running:
            self.state_container.complete()
            
        return normalized_items

    def run_current_flow(
        self,
        run_id: str,
        executor: Callable[[Mapping[str, Any]], Any],
        rows: Optional[Sequence[Mapping[str, Any]]] = None,
        on_result: Optional[Callable[[BenchmarkResultItem], None]] = None,
    ) -> list[BenchmarkResultItem]:
        source_rows = list(rows) if rows is not None else self.load_source_rows()
        self.state_container.start(run_id, len(source_rows))

        normalized_items: list[BenchmarkResultItem] = []

        try:
            if self.state_container.should_stop():
                if getattr(self.state_container, "state", None) == BenchmarkState.stopping:
                    self.state_container.finalize_stopped()
                return normalized_items

            self.state_container.transition_to_running()

            for index, source_row in enumerate(source_rows, start=1):
                if self.state_container.should_stop():
                    if getattr(self.state_container, "state", None) == BenchmarkState.stopping:
                        self.state_container.finalize_stopped()
                    return normalized_items

                started = time.perf_counter()
                execution_result = executor(source_row)
                elapsed_seconds = time.perf_counter() - started

                if isinstance(execution_result, Mapping):
                    if execution_result.get("stop_requested"):
                        if getattr(self.state_container, "state", None) == BenchmarkState.stopping:
                            self.state_container.finalize_stopped()
                        return normalized_items
                    merged_row = dict(source_row)
                    merged_row.update(execution_result)
                    raw_score = execution_result.get("raw_score")
                    metrics = execution_result.get("metrics")
                    mode_answers = execution_result.get("mode_answers")
                    mode_scores = execution_result.get("mode_scores")
                    mode_metrics = execution_result.get("mode_metrics")
                    primary_mode = execution_result.get("primary_mode")
                    completed_at = execution_result.get("completed_at")
                else:
                    merged_row = dict(source_row)
                    merged_row[self.result_column] = execution_result
                    raw_score = None
                    metrics = None
                    mode_answers = None
                    mode_scores = None
                    mode_metrics = None
                    primary_mode = None
                    completed_at = None

                item = self.record_result(
                    merged_row,
                    question_index=index,
                    on_result=on_result,
                    raw_score=raw_score,
                    metrics=metrics,
                    mode_answers=mode_answers,
                    mode_scores=mode_scores,
                    mode_metrics=mode_metrics,
                    primary_mode=primary_mode,
                    completed_at=completed_at,
                    elapsed_seconds=elapsed_seconds,
                )
                normalized_items.append(item)

            if self.state_container.should_stop():
                if getattr(self.state_container, "state", None) == BenchmarkState.stopping:
                    self.state_container.finalize_stopped()
                return normalized_items

            self.state_container.complete()
            return normalized_items
        except Exception as exc:
            self.state_container.fail(str(exc))
            raise
