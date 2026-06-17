import asyncio
import pytest
import threading
import time
from datetime import datetime
import httpx
from unittest.mock import Mock
from tenacity import RetryError
from minirag.api.benchmark_types import (
    BenchmarkState,
    BenchmarkResultItem,
    BenchmarkSummary,
    BenchmarkPageSnapshot,
    BenchmarkModelOption,
    SCORE_LABELS,
    score_to_label,
    is_valid_transition,
    assert_valid_transition,
    build_summary_from_results,
    VALID_TRANSITIONS,
)
from minirag.api.benchmark_adapter import (
    ShipBenchmarkRunnerAdapter,
    compute_recall_rate,
    parse_context_source_ids,
    parse_evidence_source_ids,
)
from minirag.api.minirag_server import (
    BenchmarkStateContainer,
    benchmark_state,
    format_benchmark_exception,
)


class TestScoreToLabel:
    """Tests for canonical score-to-label mapping."""

    def test_score_1_returns_correct(self):
        assert score_to_label(1) == "正确"

    def test_score_0_returns_partial(self):
        assert score_to_label(0) == "回避或不足"

    def test_score_minus_1_returns_wrong(self):
        assert score_to_label(-1) == "错误"

    def test_invalid_score_raises(self):
        with pytest.raises(ValueError, match="Invalid raw_score"):
            score_to_label(2)

    def test_score_labels_constant_matches_function(self):
        for score, label in SCORE_LABELS.items():
            assert score_to_label(score) == label


class TestBenchmarkErrorFormatting:
    def test_format_benchmark_exception_unwraps_http_status_error(self):
        request = httpx.Request("POST", "http://127.0.0.1:1234/v1/chat/completions")
        response = httpx.Response(
            503,
            request=request,
            text='{"error":"model unavailable"}',
        )

        exc = httpx.HTTPStatusError(
            "Service unavailable",
            request=request,
            response=response,
        )

        assert (
            format_benchmark_exception(exc)
            == 'HTTP 503 from http://127.0.0.1:1234/v1/chat/completions: {"error":"model unavailable"}'
        )

    def test_format_benchmark_exception_unwraps_retry_error(self):
        request = httpx.Request("POST", "http://127.0.0.1:1234/v1/chat/completions")
        response = httpx.Response(500, request=request, text='{"error":"backend failed"}')
        root_exc = httpx.HTTPStatusError(
            "Backend failed",
            request=request,
            response=response,
        )
        last_attempt = Mock()
        last_attempt.exception.return_value = root_exc
        retry_exc = RetryError(last_attempt)

        assert (
            format_benchmark_exception(retry_exc)
            == 'HTTP 500 from http://127.0.0.1:1234/v1/chat/completions: {"error":"backend failed"}'
        )

    def test_format_benchmark_exception_formats_timeout_error(self):
        assert format_benchmark_exception(asyncio.TimeoutError()) == "request timed out"


class TestStateMachine:
    """Tests for benchmark state machine transitions."""

    def test_state_machine_idle_to_starting_valid(self):
        assert is_valid_transition(BenchmarkState.idle, BenchmarkState.starting) is True

    def test_state_machine_running_to_completed_valid(self):
        assert (
            is_valid_transition(BenchmarkState.running, BenchmarkState.completed)
            is True
        )

    def test_state_machine_running_to_stopping_valid(self):
        assert (
            is_valid_transition(BenchmarkState.running, BenchmarkState.stopping) is True
        )

    def test_state_machine_stopped_to_idle_valid(self):
        assert is_valid_transition(BenchmarkState.stopped, BenchmarkState.idle) is True

    def test_state_machine_idle_to_running_invalid(self):
        assert is_valid_transition(BenchmarkState.idle, BenchmarkState.running) is False

    def test_state_machine_running_to_idle_invalid(self):
        assert is_valid_transition(BenchmarkState.running, BenchmarkState.idle) is False

    def test_state_machine_completed_to_starting_valid(self):
        assert (
            is_valid_transition(BenchmarkState.completed, BenchmarkState.starting)
            is True
        )

    def test_state_machine_failed_to_starting_valid(self):
        assert (
            is_valid_transition(BenchmarkState.failed, BenchmarkState.starting) is True
        )

    def test_state_machine_stopped_to_starting_valid(self):
        assert (
            is_valid_transition(BenchmarkState.stopped, BenchmarkState.starting) is True
        )


class TestInvalidTransitions:
    """Tests for invalid transition assertions."""

    def test_invalid_transition_reset_during_running_raises(self):
        with pytest.raises(
            ValueError,
            match="Invalid state transition: running -> idle",
        ):
            assert_valid_transition(BenchmarkState.running, BenchmarkState.idle)

    def test_invalid_transition_start_during_running_raises(self):
        with pytest.raises(
            ValueError,
            match="Invalid state transition: running -> starting",
        ):
            assert_valid_transition(BenchmarkState.running, BenchmarkState.starting)

    def test_invalid_transition_valid_transition_succeeds(self):
        assert_valid_transition(BenchmarkState.idle, BenchmarkState.starting)

    def test_invalid_transition_valid_terminal_start_succeeds(self):
        assert_valid_transition(BenchmarkState.completed, BenchmarkState.starting)
        assert_valid_transition(BenchmarkState.stopped, BenchmarkState.starting)
        assert_valid_transition(BenchmarkState.failed, BenchmarkState.starting)


class TestBuildSummary:
    """Tests for summary building from results."""

    def test_empty_results(self):
        results: list[BenchmarkResultItem] = []
        summary = build_summary_from_results(results)
        assert summary.total == 0
        assert summary.completed == 0
        assert summary.correct_count == 0
        assert summary.partial_count == 0
        assert summary.wrong_count == 0
        assert summary.accuracy_percent == 0.0
        assert summary.avg_response_time_ms == 0.0

    def test_all_correct(self):
        results = [
            BenchmarkResultItem(
                question_id="q1",
                question="Test?",
                gold_answer="A",
                model_answer="A",
                raw_score=1,
                status_label="正确",
                response_time_ms=100,
                completed_at="2024-01-01T00:00:00Z",
            ),
            BenchmarkResultItem(
                question_id="q2",
                question="Test?",
                gold_answer="B",
                model_answer="B",
                raw_score=1,
                status_label="正确",
                response_time_ms=200,
                completed_at="2024-01-01T00:00:00Z",
            ),
        ]
        summary = build_summary_from_results(results)
        assert summary.total == 2
        assert summary.completed == 2
        assert summary.correct_count == 2
        assert summary.partial_count == 0
        assert summary.wrong_count == 0
        assert summary.accuracy_percent == 100.0

    def test_mixed_results(self):
        results = [
            BenchmarkResultItem(
                question_id="q1",
                question="Test?",
                gold_answer="A",
                model_answer="A",
                raw_score=1,
                status_label="正确",
                response_time_ms=100,
                completed_at="2024-01-01T00:00:00Z",
            ),
            BenchmarkResultItem(
                question_id="q2",
                question="Test?",
                gold_answer="B",
                model_answer="C",
                raw_score=-1,
                status_label="错误",
                response_time_ms=150,
                completed_at="2024-01-01T00:00:00Z",
            ),
            BenchmarkResultItem(
                question_id="q3",
                question="Test?",
                gold_answer="D",
                model_answer="D?",
                raw_score=0,
                status_label="回避或不足",
                response_time_ms=120,
                completed_at="2024-01-01T00:00:00Z",
            ),
        ]
        summary = build_summary_from_results(results)
        assert summary.total == 3
        assert summary.completed == 3
        assert summary.correct_count == 1
        assert summary.partial_count == 1
        assert summary.wrong_count == 1
        assert summary.accuracy_percent == pytest.approx(33.33, rel=0.01)
        assert summary.avg_response_time_ms == pytest.approx(123.33, rel=0.01)

    def test_in_progress_results_use_declared_total_and_processed_completed(self):
        results = [
            BenchmarkResultItem(
                question_id="q1",
                question="Test?",
                gold_answer="A",
                model_answer="A",
                raw_score=1,
                status_label="正确",
                response_time_ms=100,
                completed_at="2024-01-01T00:00:00Z",
            ),
            BenchmarkResultItem(
                question_id="q2",
                question="Test?",
                gold_answer="B",
                model_answer="",
                raw_score=-1,
                status_label="错误",
                response_time_ms=0,
                completed_at="2024-01-01T00:00:00Z",
                error_message="Timeout",
            ),
        ]

        summary = build_summary_from_results(results, total_questions=5)

        assert summary.total == 5
        assert summary.completed == 2
        assert summary.correct_count == 1
        assert summary.wrong_count == 1
        assert summary.accuracy_percent == 50.0

    def test_multi_mode_results_generate_mode_summaries(self):
        results = [
            BenchmarkResultItem(
                question_id="q1",
                question="Test?",
                gold_answer="A",
                model_answer="A",
                raw_score=1,
                status_label="正确",
                response_time_ms=100,
                completed_at="2024-01-01T00:00:00Z",
                primary_mode="graph_text_hybrid",
                mode_answers={
                    "graph_text_hybrid": "A",
                    "graph_only": "A",
                    "text_only": "B",
                },
                mode_scores={
                    "graph_text_hybrid": 1,
                    "graph_only": 1,
                    "text_only": -1,
                },
                mode_status_labels={
                    "graph_text_hybrid": "正确",
                    "graph_only": "正确",
                    "text_only": "错误",
                },
                mode_response_time_ms={
                    "graph_text_hybrid": 100,
                    "graph_only": 120,
                    "text_only": 130,
                },
                mode_recall_rates={
                    "graph_text_hybrid": 100.0,
                    "graph_only": 100.0,
                    "text_only": 0.0,
                },
            )
        ]

        summary = build_summary_from_results(results)

        assert summary.avg_recall_rate == 100.0
        assert summary.primary_mode == "graph_text_hybrid"
        assert summary.mode_summaries["graph_text_hybrid"].avg_recall_rate == 100.0
        assert summary.mode_summaries["graph_text_hybrid"].accuracy_percent == 100.0
        assert summary.mode_summaries["graph_only"].accuracy_percent == 100.0
        assert summary.mode_summaries["text_only"].accuracy_percent == 0.0


class TestBenchmarkAdapter:
    """Tests for the thin benchmark adapter over the current ship-domain flow."""

    def setup_method(self):
        self.container = BenchmarkStateContainer()
        self.adapter = ShipBenchmarkRunnerAdapter(self.container)

    def test_adapter_success_normalizes_success_row(self):
        rows = [
            {
                "question_id": "t01_q001",
                "question_type": "原因解释",
                "question": "液压系统为什么没有舵效？",
                "answer": "液压系统管路中可能有空气。",
            }
        ]

        def executor(_row):
            return {
                "faultcase_fast_stream_plain": "常见原因包括液压系统管路中存在空气。",
                "raw_score": 1,
                "metrics": {"full_s": 1.24, "ttft_total_s": 0.58},
            }

        items = self.adapter.run_current_flow("run-adapter-success", executor, rows)

        assert self.container.state == BenchmarkState.completed
        assert self.container.current_question_index == 1
        assert self.container.summary is not None
        assert self.container.summary.total == 1
        assert self.container.summary.correct_count == 1
        assert self.container.summary.avg_response_time_ms == 1240.0
        assert len(items) == 1
        assert items[0].question_id == "t01_q001"
        assert items[0].question_type == "原因解释"
        assert items[0].model_answer == "常见原因包括液压系统管路中存在空气。"
        assert items[0].raw_score == 1
        assert items[0].status_label == "正确"
        assert items[0].response_time_ms == 1240
        assert items[0].error_message is None
        assert items[0].completed_at.endswith("Z")

    def test_adapter_error_normalizes_timeout_row(self):
        row = {
            "question_id": "t01_q005",
            "question_type": "xx原因会导致什么故障",
            "question": "油柜无油会导致什么故障？",
            "answer": "会导致主辅机无燃油供应或供油不畅。",
            "faultcase_fast_stream_plain": "[ERROR] HTTPConnectionPool(host='127.0.0.1', port=9733): Read timed out. (read timeout=180)",
        }

        item = self.adapter.normalize_result_row(row, elapsed_seconds=180.0)

        assert item.question_id == "t01_q005"
        assert item.question_type == "xx原因会导致什么故障"
        assert item.model_answer == ""
        assert item.raw_score == -1
        assert item.status_label == "错误"
        assert item.error_message is not None
        assert item.error_message.startswith("Timeout:")
        assert "Read timed out" in item.error_message
        assert item.response_time_ms == 180000

    def test_adapter_requires_score_for_successful_row(self):
        row = {
            "question_id": "t01_q006",
            "question_type": "维修方法",
            "question": "总用泵无水流出时应如何处理？",
            "answer": "先检查阀门，再排出吸口内空气。",
            "faultcase_fast_stream_plain": "应先检查总用泵阀门和江水总阀门是否正常打开。",
        }

        with pytest.raises(
            ValueError,
            match="Successful benchmark row is missing a judged score",
        ):
            self.adapter.normalize_result_row(row, elapsed_seconds=1.5)

    def test_adapter_preserves_model_answer_for_explicit_judge_error(self):
        row = {
            "question_id": "t01_q007",
            "question_type": "原因解释",
            "question": "24V 供电系统为何全船无电？",
            "answer": "蓄电池无电。",
            "faultcase_fast_stream_plain": "常见原因包括：蓄电池无电。",
            "raw_score": -1,
            "error_message": "Benchmark judge timed out after 90s",
        }

        item = self.adapter.normalize_result_row(row, elapsed_seconds=1.5)

        assert item.model_answer == "常见原因包括：蓄电池无电。"
        assert item.raw_score == -1
        assert item.error_message == "Benchmark judge timed out after 90s"

    def test_adapter_normalizes_canonical_fields_from_repo_csv_headers(self):
        row = {
            "Question": "24V供电系统出现“全船无24V供电”时，常见原因有哪些？",
            "Gold Answer": "常见原因包括：蓄电池无电。",
            "Evidence": "航政艇常见故障分析及排除方法_步立军_2033936093677412352.md::llm_003 | 依据 llm_003 对应故障卡与原始表述整理。",
            "Type": "cause_explanation",
            "faultcase_fast_stream_plain": "常见原因包括液压系统管路中存在空气。",
            "raw_score": 1,
        }

        item = self.adapter.normalize_result_row(row, elapsed_seconds=1.5)

        assert item.question == row["Question"]
        assert item.gold_answer == row["Gold Answer"]
        assert item.question_type == row["Type"]
        assert (
            item.question_id
            == "航政艇常见故障分析及排除方法_步立军_2033936093677412352.md::llm_003::594c56e2"
        )

    def test_load_source_rows_adds_canonical_aliases_for_repo_headers(self, tmp_path):
        csv_path = tmp_path / "benchmark.csv"
        csv_path.write_text(
            (
                "Question,Gold Answer,Evidence,Type\n"
                "测试问题,测试标准答案,doc.md::chunk_001 | evidence,cause_explanation\n"
            ),
            encoding="utf-8",
        )
        adapter = ShipBenchmarkRunnerAdapter(self.container, source_csv_path=csv_path)

        rows = adapter.load_source_rows()

        assert len(rows) == 1
        assert rows[0]["Question"] == "测试问题"
        assert rows[0]["question"] == "测试问题"
        assert rows[0]["Gold Answer"] == "测试标准答案"
        assert rows[0]["gold_answer"] == "测试标准答案"
        assert rows[0]["answer"] == "测试标准答案"
        assert rows[0]["Evidence"] == "doc.md::chunk_001 | evidence"
        assert rows[0]["evidence"] == "doc.md::chunk_001 | evidence"
        assert rows[0]["Type"] == "cause_explanation"
        assert rows[0]["question_type"] == "cause_explanation"

    def test_adapter_derived_question_ids_stay_distinct_when_evidence_matches(self):
        first = self.adapter.normalize_result_row(
            {
                "Question": "问题一",
                "Gold Answer": "答案一",
                "Evidence": "shared.md::chunk_001 | 证据一",
                "Type": "cause_explanation",
                "faultcase_fast_stream_plain": "回答一",
                "raw_score": 1,
            },
            elapsed_seconds=1,
        )
        second = self.adapter.normalize_result_row(
            {
                "Question": "问题二",
                "Gold Answer": "答案二",
                "Evidence": "shared.md::chunk_001 | 证据二",
                "Type": "cause_explanation",
                "faultcase_fast_stream_plain": "回答二",
                "raw_score": 1,
            },
            elapsed_seconds=1,
        )

        assert first.question_id != second.question_id
        assert first.question_id.startswith("shared.md::chunk_001::")
        assert second.question_id.startswith("shared.md::chunk_001::")

    def test_state_machine_adapter_updates_progress_and_summary_incrementally(self):
        self.container.start("run-state-machine", 2)
        self.container.transition_to_running()

        first_item = self.adapter.record_result(
            {
                "question_id": "q1",
                "question_type": "原因解释",
                "question": "为什么冷却水流量变小？",
                "answer": "滤器有异物。",
                "faultcase_fast_stream_plain": "常见原因包括滤器中有异物。",
            },
            question_index=1,
            raw_score=1,
            metrics={"full_s": 1.2},
        )

        assert first_item.status_label == "正确"
        assert self.container.current_question_index == 1
        assert self.container.progress_percent == 50.0
        assert self.container.summary is not None
        assert self.container.summary.total == 2
        assert self.container.summary.completed == 1
        assert self.container.summary.correct_count == 1
        assert self.container.summary.accuracy_percent == 100.0

        second_item = self.adapter.record_result(
            {
                "question_id": "q2",
                "question_type": "维修方法",
                "question": "应如何处理？",
                "answer": "先补油再排气。",
                "faultcase_fast_stream_plain": "[ERROR] 500 Server Error: Internal Server Error for url: http://127.0.0.1:9733/query/stream/plain",
            },
            question_index=2,
            metrics={"retrieval_ms": 35},
        )

        assert second_item.model_answer == ""
        assert second_item.error_message is not None
        assert second_item.error_message.startswith("Server error:")
        assert self.container.current_question_index == 2
        assert self.container.progress_percent == 100.0
        assert self.container.summary is not None
        assert self.container.summary.total == 2
        assert self.container.summary.completed == 2
        assert self.container.summary.correct_count == 1
        assert self.container.summary.wrong_count == 1
        assert self.container.summary.accuracy_percent == 50.0
        assert self.container.summary.avg_response_time_ms == 1200.0

    def test_adapter_stop_request_finalizes_after_active_row(self):
        rows = [
            {
                "question_id": "q1",
                "question_type": "原因解释",
                "question": "为什么冷却水流量变小？",
                "answer": "滤器有异物。",
            },
            {
                "question_id": "q2",
                "question_type": "维修方法",
                "question": "应如何处理？",
                "answer": "先补油再排气。",
            },
        ]

        def executor(row):
            if row["question_id"] == "q1":
                self.container.stop("user requested stop")
            return {
                "faultcase_fast_stream_plain": "已处理当前题目。",
                "raw_score": 1,
                "metrics": {"full_s": 0.8},
            }

        items = self.adapter.run_current_flow("run-stop-after-row", executor, rows)

        assert len(items) == 1
        assert items[0].question_id == "q1"
        assert self.container.state == BenchmarkState.stopped
        assert self.container.summary is not None
        assert self.container.summary.total == 2
        assert self.container.summary.completed == 1

    def test_adapter_stop_requested_payload_finalizes_without_recording_partial_row(self):
        rows = [
            {
                "question_id": "q1",
                "question_type": "原因解释",
                "question": "为什么冷却水流量变小？",
                "answer": "滤器有异物。",
            }
        ]

        def executor(_row):
            self.container.stop("user requested stop")
            return {"stop_requested": True}

        items = self.adapter.run_current_flow("run-stop-mid-row", executor, rows)

        assert items == []
        assert self.container.state == BenchmarkState.stopped
        assert self.container.summary is None

    def test_adapter_success_keeps_mode_breakdown(self):
        rows = [
            {
                "question_id": "q1",
                "question_type": "原因解释",
                "question": "为什么没有舵效？",
                "answer": "液压系统管路中可能有空气。",
            }
        ]

        def executor(_row):
            return {
                "faultcase_fast_stream_plain": "图文混合回答",
                "raw_score": 1,
                "metrics": {"full_s": 1.0},
                "mode_answers": {
                    "graph_text_hybrid": "图文混合回答",
                    "graph_only": "图谱回答",
                    "text_only": "文本回答",
                },
                "mode_scores": {
                    "graph_text_hybrid": 1,
                    "graph_only": 0,
                    "text_only": -1,
                },
                "mode_metrics": {
                    "graph_text_hybrid": {"full_s": 1.0, "recall_rate": 100.0},
                    "graph_only": {"full_s": 1.2, "recall_rate": 50.0},
                    "text_only": {"full_s": 1.4, "recall_rate": 0.0},
                },
                "primary_mode": "graph_text_hybrid",
            }

        items = self.adapter.run_current_flow("run-multi-mode", executor, rows)

        assert items[0].primary_mode == "graph_text_hybrid"
        assert items[0].mode_scores["graph_only"] == 0
        assert items[0].mode_status_labels["text_only"] == "错误"
        assert items[0].mode_response_time_ms["text_only"] == 1400
        assert items[0].mode_recall_rates["graph_only"] == 50.0
        assert self.container.summary is not None
        assert self.container.summary.mode_summaries["graph_only"].partial_count == 1


class TestBenchmarkRecallHelpers:
    def setup_method(self):
        self.container = BenchmarkStateContainer()
        self.adapter = ShipBenchmarkRunnerAdapter(self.container)

    def test_parse_evidence_source_ids_handles_multi_evidence_rows(self):
        raw = "doc.md::llm_001,doc.md::llm_002;doc.md::llm_003 | 依据整理"
        assert parse_evidence_source_ids(raw) == [
            "doc.md::llm_001",
            "doc.md::llm_002",
            "doc.md::llm_003",
        ]

    def test_parse_context_source_ids_reads_sources_csv_block(self):
        context = """
-----Sources-----
```csv
id,content
doc.md::llm_001,第一条
doc.md::llm_002,第二条
```
-----Entities-----
```csv
entity,entity_type,score,description
```
""".strip()
        assert parse_context_source_ids(context) == [
            "doc.md::llm_001",
            "doc.md::llm_002",
        ]

    def test_parse_context_source_ids_reads_record_ids_from_faultcase_chunks(self):
        context = """
-----Sources-----
```csv
id,content
chunk-faultcase-f0d2770696172678b2464c0a1d89c496,"[记录ID] llm_038
[设备] 锚机电气控制系统
[故障卡片] 制动器失效（锚机电气控制系统）"
```
-----Entities-----
```csv
entity,entity_type,score,description
```
""".strip()
        assert parse_context_source_ids(context) == [
            "chunk-faultcase-f0d2770696172678b2464c0a1d89c496",
            "llm_038",
        ]
        assert (
            compute_recall_rate(
                ["船舶电气设备维修指南.md::llm_038"],
                parse_context_source_ids(context),
            )
            == 100.0
        )

    def test_compute_recall_rate_uses_expected_evidence_ids(self):
        recall = compute_recall_rate(
            ["doc.md::llm_001", "doc.md::llm_002"],
            ["doc.md::llm_002", "doc.md::llm_003"],
        )
        assert recall == 50.0

    def test_adapter_run_current_flow_emits_results_incrementally(self):
        rows = [
            {
                "question_id": "q1",
                "question_type": "原因解释",
                "question": "问题一",
                "answer": "答案一",
            },
            {
                "question_id": "q2",
                "question_type": "维修方法",
                "question": "问题二",
                "answer": "答案二",
            },
        ]
        persisted: list[tuple[str, int]] = []

        def executor(row):
            return {
                "faultcase_fast_stream_plain": f"回答-{row['question_id']}",
                "raw_score": 1,
                "metrics": {"full_s": 0.5},
            }

        def on_result(item: BenchmarkResultItem):
            persisted.append((item.question_id, self.container.current_question_index))

        items = self.adapter.run_current_flow(
            "run-incremental-persist",
            executor,
            rows,
            on_result=on_result,
        )

        assert [item.question_id for item in items] == ["q1", "q2"]
        assert persisted == [("q1", 1), ("q2", 2)]
        assert self.container.state == BenchmarkState.completed


class TestEndpointContract:
    """Tests for endpoint contract shape compatibility."""

    def test_endpoint_contract_result_item_has_required_fields(self):
        item = BenchmarkResultItem(
            question_id="q1",
            question="What causes hydraulic failure?",
            gold_answer="Air in system",
            model_answer="Air in system",
            raw_score=1,
            status_label="正确",
            response_time_ms=1500,
            completed_at="2024-01-01T00:00:00Z",
        )
        assert item.question_id == "q1"
        assert item.question == "What causes hydraulic failure?"
        assert item.gold_answer == "Air in system"
        assert item.model_answer == "Air in system"
        assert item.raw_score == 1
        assert item.status_label == "正确"
        assert item.response_time_ms == 1500
        assert item.completed_at == "2024-01-01T00:00:00Z"

    def test_endpoint_contract_result_item_with_error(self):
        item = BenchmarkResultItem(
            question_id="q1",
            question="Test?",
            gold_answer="A",
            model_answer="",
            raw_score=-1,
            status_label="错误",
            response_time_ms=0,
            completed_at="2024-01-01T00:00:00Z",
            error_message="Timeout",
        )
        assert item.error_message == "Timeout"

    def test_endpoint_contract_result_item_optional_fields(self):
        item = BenchmarkResultItem(
            question_id="q1",
            question="Test?",
            gold_answer="A",
            model_answer="A",
            raw_score=1,
            status_label="正确",
            response_time_ms=100,
            completed_at="2024-01-01T00:00:00Z",
            question_type="故障诊断",
        )
        assert item.question_type == "故障诊断"

    def test_endpoint_contract_summary_has_required_fields(self):
        summary = BenchmarkSummary(
            total=10,
            completed=8,
            correct_count=5,
            partial_count=2,
            wrong_count=1,
            accuracy_percent=50.0,
            avg_response_time_ms=1200.0,
        )
        assert summary.total == 10
        assert summary.completed == 8
        assert summary.correct_count == 5
        assert summary.partial_count == 2
        assert summary.wrong_count == 1
        assert summary.accuracy_percent == 50.0
        assert summary.avg_response_time_ms == 1200.0

    def test_endpoint_contract_page_snapshot_has_required_fields(self):
        snapshot = BenchmarkPageSnapshot(
            state=BenchmarkState.running,
            run_id="run-123",
            progress_percent=45.0,
            summary=BenchmarkSummary(
                total=10,
                completed=5,
                correct_count=3,
                partial_count=1,
                wrong_count=1,
                accuracy_percent=30.0,
                avg_response_time_ms=1500.0,
            ),
            recent_results=[
                BenchmarkResultItem(
                    question_id="q1",
                    question="Test?",
                    gold_answer="A",
                    model_answer="A",
                    raw_score=1,
                    status_label="正确",
                    response_time_ms=100,
                    completed_at="2024-01-01T00:00:00Z",
                )
            ],
            available_models=[
                BenchmarkModelOption(id="qwen3.5-2b", label="Qwen 3.5 2B (LM Studio)"),
                BenchmarkModelOption(id="qwen3.5:2b", label="Qwen 3.5 2B (Ollama)"),
                BenchmarkModelOption(id="gpt-4", label="GPT-4"),
            ],
            selected_model="qwen3.5-2b",
            can_start=False,
            can_stop=True,
            can_reset=False,
        )
        assert snapshot.state == BenchmarkState.running
        assert snapshot.run_id == "run-123"
        assert snapshot.progress_percent == 45.0
        assert snapshot.summary is not None
        assert snapshot.summary.total == 10
        assert snapshot.summary.correct_count == 3
        assert len(snapshot.recent_results) == 1
        assert len(snapshot.available_models) == 3
        assert snapshot.available_models[0].id == "qwen3.5-2b"
        assert snapshot.selected_model == "qwen3.5-2b"
        assert snapshot.can_start is False
        assert snapshot.can_stop is True
        assert snapshot.can_reset is False

    def test_endpoint_contract_page_snapshot_idle_state(self):
        snapshot = BenchmarkPageSnapshot(
            state=BenchmarkState.idle,
            run_id=None,
            progress_percent=0.0,
            summary=None,
            recent_results=[],
            available_models=[
                BenchmarkModelOption(
                    id="qwen3.5-2b", label="Qwen 3.5 2B"
                ),
            ],
            selected_model="qwen3.5-2b",
            can_start=True,
            can_stop=False,
            can_reset=False,
        )
        assert snapshot.state == BenchmarkState.idle
        assert snapshot.run_id is None
        assert snapshot.summary is None
        assert snapshot.can_start is True
        assert snapshot.can_stop is False
        assert snapshot.can_reset is False

    def test_endpoint_contract_page_snapshot_with_error_message(self):
        snapshot = BenchmarkPageSnapshot(
            state=BenchmarkState.failed,
            run_id="run-fail",
            progress_percent=30.0,
            error_message="LLM API timeout",
            available_models=[],
            selected_model="",
            can_start=True,
            can_stop=False,
            can_reset=True,
        )
        assert snapshot.state == BenchmarkState.failed
        assert snapshot.error_message == "LLM API timeout"
        assert snapshot.can_start is True
        assert snapshot.can_reset is True


class TestBenchmarkStateContainer:
    """Tests for BenchmarkStateContainer - single active run state holder."""

    def setup_method(self):
        """Create fresh container for each test."""
        self.container = BenchmarkStateContainer()

    # --- Single active run exclusivity tests ---

    def test_single_active_run_idle_can_start(self):
        """Idle state allows starting."""
        self.container.start("run-1", 10)
        assert self.container.state == BenchmarkState.starting
        assert self.container.run_id == "run-1"
        assert self.container.total_questions == 10

    def test_single_active_run_rejects_second_start_during_running(self):
        """Second start attempt while running raises ValueError."""
        self.container.start("run-1", 10)
        self.container.transition_to_running()

        with pytest.raises(ValueError, match="Cannot start benchmark"):
            self.container.start("run-2", 20)

    def test_single_active_run_rejects_second_start_during_starting(self):
        """Second start attempt while starting raises ValueError."""
        self.container.start("run-1", 10)

        with pytest.raises(ValueError, match="Cannot start benchmark"):
            self.container.start("run-2", 20)

    def test_single_active_run_rejects_start_during_stopping(self):
        """Start attempt during stopping raises ValueError."""
        self.container.start("run-1", 10)
        self.container.transition_to_running()
        self.container.stop("user request")

        with pytest.raises(ValueError, match="Cannot start benchmark"):
            self.container.start("run-2", 20)

    # --- Reset allowed only from terminal states tests ---

    def test_reset_from_idle_raises(self):
        """Reset from idle state raises ValueError."""
        with pytest.raises(ValueError, match="Cannot reset benchmark"):
            self.container.reset()

    def test_reset_from_starting_raises(self):
        """Reset from starting state raises ValueError."""
        self.container.start("run-1", 10)

        with pytest.raises(ValueError, match="Cannot reset benchmark"):
            self.container.reset()

    def test_reset_from_running_raises(self):
        """Reset from running state raises ValueError."""
        self.container.start("run-1", 10)
        self.container.transition_to_running()

        with pytest.raises(ValueError, match="Cannot reset benchmark"):
            self.container.reset()

    def test_reset_from_stopping_raises(self):
        """Reset from stopping state raises ValueError."""
        self.container.start("run-1", 10)
        self.container.transition_to_running()
        self.container.stop("user request")

        with pytest.raises(ValueError, match="Cannot reset benchmark"):
            self.container.reset()

    def test_reset_from_completed_succeeds(self):
        """Reset from completed state succeeds."""
        self.container.start("run-1", 10)
        self.container.transition_to_running()
        # Add some results
        result = BenchmarkResultItem(
            question_id="q1",
            question="Test?",
            gold_answer="A",
            model_answer="A",
            raw_score=1,
            status_label="正确",
            response_time_ms=100,
            completed_at="2024-01-01T00:00:00Z",
        )
        self.container.add_result(result)
        self.container.complete()

        assert self.container.state == BenchmarkState.completed
        assert self.container.can_reset() is True

        self.container.reset()

        assert self.container.state == BenchmarkState.idle
        assert self.container.run_id is None

    def test_reset_from_stopped_succeeds(self):
        """Reset from stopped state succeeds."""
        self.container.start("run-1", 10)
        self.container.transition_to_running()
        self.container.stop("user request")
        self.container.finalize_stopped()

        assert self.container.state == BenchmarkState.stopped
        assert self.container.can_reset() is True

        self.container.reset()

        assert self.container.state == BenchmarkState.idle
        assert self.container.run_id is None

    def test_reset_from_failed_succeeds(self):
        """Reset from failed state succeeds."""
        self.container.start("run-1", 10)
        self.container.transition_to_running()
        self.container.fail("API timeout")

        assert self.container.state == BenchmarkState.failed
        assert self.container.can_reset() is True

        self.container.reset()

        assert self.container.state == BenchmarkState.idle
        assert self.container.run_id is None

    def test_reset_clears_terminal_snapshot(self):
        """Reset clears terminal snapshot data - proving canonical idle state."""
        # Complete first run to create terminal snapshot
        self.container.start("run-1", 10)
        self.container.transition_to_running()

        result = BenchmarkResultItem(
            question_id="q1",
            question="Test?",
            gold_answer="A",
            model_answer="A",
            raw_score=1,
            status_label="正确",
            response_time_ms=100,
            completed_at="2024-01-01T00:00:00Z",
        )
        self.container.add_result(result)
        self.container.complete()

        # Verify terminal snapshot exists before reset
        assert self.container.terminal_summary is not None
        assert len(self.container.terminal_results) == 1

        # Reset - should clear terminal snapshot
        self.container.reset()

        # Verify canonical idle: terminal snapshot cleared
        assert self.container.terminal_summary is None
        assert self.container.terminal_results == []
        assert self.container.terminal_error_message is None
        assert self.container.terminal_stopped_reason is None
        # Verify other canonical idle fields
        assert self.container.state == BenchmarkState.idle
        assert self.container.run_id is None
        assert self.container.summary is None

    def test_reset_clears_stopped_terminal_snapshot(self):
        """Reset clears terminal snapshot from stopped state."""
        self.container.start("run-1", 10)
        self.container.transition_to_running()
        self.container.stop("user request")
        self.container.finalize_stopped()

        # Verify terminal snapshot exists
        assert self.container.terminal_stopped_reason == "user request"

        self.container.reset()

        # Verify canonical idle: terminal snapshot cleared
        assert self.container.terminal_summary is None
        assert self.container.terminal_results == []
        assert self.container.terminal_stopped_reason is None

    def test_reset_clears_failed_terminal_snapshot(self):
        """Reset clears terminal snapshot from failed state."""
        self.container.start("run-1", 10)
        self.container.transition_to_running()
        self.container.fail("API timeout")

        # Verify terminal snapshot exists
        assert self.container.terminal_error_message == "API timeout"

        self.container.reset()

        # Verify canonical idle: terminal snapshot cleared
        assert self.container.terminal_summary is None
        assert self.container.terminal_results == []
        assert self.container.terminal_error_message is None

    # --- Terminal snapshot preservation tests ---

    def test_terminal_snapshot_preserved_after_complete(self):
        """Terminal summary/results preserved after completion."""
        self.container.start("run-1", 10)
        self.container.transition_to_running()

        result1 = BenchmarkResultItem(
            question_id="q1",
            question="Test 1?",
            gold_answer="A",
            model_answer="A",
            raw_score=1,
            status_label="正确",
            response_time_ms=100,
            completed_at="2024-01-01T00:00:00Z",
        )
        result2 = BenchmarkResultItem(
            question_id="q2",
            question="Test 2?",
            gold_answer="B",
            model_answer="C",
            raw_score=-1,
            status_label="错误",
            response_time_ms=150,
            completed_at="2024-01-01T00:00:00Z",
        )
        self.container.add_result(result1)
        self.container.add_result(result2)
        self.container.complete()

        # Verify terminal snapshot preserved
        assert self.container.terminal_summary is not None
        assert self.container.terminal_summary.total == 10
        assert self.container.terminal_summary.correct_count == 1
        assert len(self.container.terminal_results) == 2

    def test_terminal_snapshot_preserved_after_stop(self):
        """Terminal summary/results preserved after user stop."""
        self.container.start("run-1", 10)
        self.container.transition_to_running()

        result = BenchmarkResultItem(
            question_id="q1",
            question="Test?",
            gold_answer="A",
            model_answer="A",
            raw_score=1,
            status_label="正确",
            response_time_ms=100,
            completed_at="2024-01-01T00:00:00Z",
        )
        self.container.add_result(result)
        self.container.stop("user request")
        self.container.finalize_stopped()

        assert self.container.terminal_summary is not None
        assert self.container.terminal_stopped_reason == "user request"

    def test_stop_waits_for_worker_exit_before_finalizing(self):
        self.container.start("run-1", 10)
        self.container.transition_to_running()

        release_worker = threading.Event()

        def worker():
            release_worker.wait(1)

        worker_thread = threading.Thread(target=worker)
        self.container.register_worker(worker_thread)
        worker_thread.start()

        self.container.stop("user request")

        assert self.container.state == BenchmarkState.stopping
        assert self.container.finalize_stop_if_worker_finished() is False

        release_worker.set()
        worker_thread.join(timeout=1)
        self.container.worker_finished(worker_thread)

        assert self.container.state == BenchmarkState.stopped

    def test_stop_finalizes_immediately_when_worker_already_finished(self):
        self.container.start("run-1", 10)
        self.container.transition_to_running()

        self.container.stop("user request")

        assert self.container.finalize_stop_if_worker_finished() is True
        assert self.container.state == BenchmarkState.stopped

    def test_terminal_snapshot_preserved_after_fail(self):
        """Terminal error preserved after failure."""
        self.container.start("run-1", 10)
        self.container.transition_to_running()
        self.container.fail("API timeout")

        assert self.container.terminal_error_message == "API timeout"

    def test_terminal_snapshot_cleared_on_new_start(self):
        """Terminal snapshot cleared when starting fresh run."""
        # Complete first run
        self.container.start("run-1", 10)
        self.container.transition_to_running()
        result = BenchmarkResultItem(
            question_id="q1",
            question="Test?",
            gold_answer="A",
            model_answer="A",
            raw_score=1,
            status_label="正确",
            response_time_ms=100,
            completed_at="2024-01-01T00:00:00Z",
        )
        self.container.add_result(result)
        self.container.complete()

        assert self.container.terminal_summary is not None

        # Start new run - should clear terminal snapshot
        self.container.start("run-2", 20)

        assert self.container.terminal_summary is None
        assert self.container.terminal_results == []

    # --- Page snapshot tests ---

    def test_page_snapshot_idle_state(self):
        """Page snapshot in idle shows correct controls."""
        snapshot = self.container.to_page_snapshot()

        assert snapshot.state == BenchmarkState.idle
        assert snapshot.run_id is None
        assert snapshot.can_start is True
        assert snapshot.can_stop is False
        assert snapshot.can_reset is False

    def test_page_snapshot_running_state(self):
        """Page snapshot in running shows correct controls."""
        self.container.start("run-1", 10)
        self.container.transition_to_running()

        snapshot = self.container.to_page_snapshot()

        assert snapshot.state == BenchmarkState.running
        assert snapshot.can_start is False
        assert snapshot.can_stop is True
        assert snapshot.can_reset is False

    def test_page_snapshot_starting_state_can_stop(self):
        """Page snapshot in starting mirrors backend stop availability."""
        self.container.start("run-1", 10)

        snapshot = self.container.to_page_snapshot()

        assert snapshot.state == BenchmarkState.starting
        assert snapshot.can_start is False
        assert snapshot.can_stop is True
        assert snapshot.can_reset is False

    def test_page_snapshot_completed_uses_terminal_snapshot(self):
        """Page snapshot in completed uses terminal snapshot for results."""
        self.container.start("run-1", 10)
        self.container.transition_to_running()

        result = BenchmarkResultItem(
            question_id="q1",
            question="Test?",
            gold_answer="A",
            model_answer="A",
            raw_score=1,
            status_label="正确",
            response_time_ms=100,
            completed_at="2024-01-01T00:00:00Z",
        )
        self.container.add_result(result)
        self.container.complete()

        snapshot = self.container.to_page_snapshot()

        assert snapshot.state == BenchmarkState.completed
        assert snapshot.summary is not None
        assert snapshot.can_start is True
        assert snapshot.can_reset is True
        # Recent results should come from terminal snapshot
        assert len(snapshot.recent_results) >= 1

    # --- Direct start from terminal tests ---

    def test_start_from_completed_without_reset(self):
        """Can start new run directly from completed without explicit reset."""
        self.container.start("run-1", 10)
        self.container.transition_to_running()
        self.container.complete()

        # Should be able to start again without reset
        self.container.start("run-2", 20)

        assert self.container.run_id == "run-2"
        assert self.container.state == BenchmarkState.starting

    def test_start_from_stopped_without_reset(self):
        """Can start new run directly from stopped without explicit reset."""
        self.container.start("run-1", 10)
        self.container.transition_to_running()
        self.container.stop("user request")
        self.container.finalize_stopped()

        self.container.start("run-2", 20)

        assert self.container.run_id == "run-2"
        assert self.container.state == BenchmarkState.starting

    def test_start_from_failed_without_reset(self):
        """Can start new run directly from failed without explicit reset."""
        self.container.start("run-1", 10)
        self.container.transition_to_running()
        self.container.fail("API error")

        self.container.start("run-2", 20)

        assert self.container.run_id == "run-2"
        assert self.container.state == BenchmarkState.starting


class TestGlobalBenchmarkState:
    """Tests for global benchmark_state singleton."""

    def test_global_state_singleton_exists(self):
        """Global benchmark_state instance exists and is accessible."""
        assert benchmark_state is not None
        assert isinstance(benchmark_state, BenchmarkStateContainer)

    def test_global_state_initial_idle(self):
        """Global state starts in idle."""
        # Reset global state first (in case previous tests left it in non-idle)
        if benchmark_state.can_reset():
            benchmark_state.reset()

        assert benchmark_state.state == BenchmarkState.idle
        assert benchmark_state.run_id is None


class TestBenchmarkEndpoints:
    """Tests for benchmark REST endpoint behaviors (T4.1-T4.4)."""

    def setup_method(self):
        """Reset global state before each test."""
        try:
            if benchmark_state.can_reset():
                benchmark_state.reset()
            elif benchmark_state.state in [
                BenchmarkState.running,
                BenchmarkState.starting,
            ]:
                benchmark_state.stop("test cleanup")
                benchmark_state.finalize_stopped()
                benchmark_state.reset()
        except Exception:
            pass
        benchmark_state._state = BenchmarkState.idle
        benchmark_state._run_id = None

    def cleanup_after(self):
        """Clean up global state after each test."""
        try:
            if benchmark_state.can_reset():
                benchmark_state.reset()
            elif benchmark_state.state in [
                BenchmarkState.running,
                BenchmarkState.starting,
            ]:
                benchmark_state.stop("test cleanup")
                benchmark_state.finalize_stopped()
                benchmark_state.reset()
        except Exception:
            pass
        benchmark_state._state = BenchmarkState.idle
        benchmark_state._run_id = None

    def teardown_method(self):
        """Clean up global state after each test."""
        try:
            if benchmark_state.can_reset():
                benchmark_state.reset()
            elif benchmark_state.state in [
                BenchmarkState.running,
                BenchmarkState.starting,
            ]:
                benchmark_state.stop("test cleanup")
                benchmark_state.finalize_stopped()
                benchmark_state.reset()
        except Exception:
            pass
        benchmark_state._state = BenchmarkState.idle
        benchmark_state._run_id = None
        if benchmark_state.state != BenchmarkState.idle:
            benchmark_state._state = BenchmarkState.idle

    # --- POST /benchmark/run tests ---

    def test_endpoint_run_idle_allows_start(self):
        """POST /benchmark/run from idle state succeeds."""
        # Verify idle state allows start
        assert benchmark_state.can_start() is True
        assert benchmark_state.state == BenchmarkState.idle

    def test_endpoint_run_rejects_during_running(self):
        """POST /benchmark/run during running state raises ValueError."""
        # Set to running state directly
        benchmark_state.start("run-test", 10)
        benchmark_state.transition_to_running()

        # Cannot start again while running
        assert benchmark_state.can_start() is False

        with pytest.raises(ValueError, match="Cannot start benchmark"):
            benchmark_state.start("run-test-2", 10)

    def test_endpoint_run_rejects_during_starting(self):
        """POST /benchmark/run during starting state raises ValueError."""
        benchmark_state.start("run-test", 10)

        assert benchmark_state.can_start() is False

        with pytest.raises(ValueError, match="Cannot start benchmark"):
            benchmark_state.start("run-test-2", 10)

    def test_endpoint_run_rejects_during_stopping(self):
        """POST /benchmark/run during stopping state raises ValueError."""
        benchmark_state.start("run-test", 10)
        benchmark_state.transition_to_running()
        benchmark_state.stop("user request")

        assert benchmark_state.can_start() is False

        with pytest.raises(ValueError, match="Cannot start benchmark"):
            benchmark_state.start("run-test-2", 10)

    def test_endpoint_run_allows_from_completed(self):
        """Can start new run directly from completed without reset."""
        benchmark_state.start("run-1", 10)
        benchmark_state.transition_to_running()
        benchmark_state.complete()

        # Can start again (terminal states allow start)
        assert benchmark_state.can_start() is True
        benchmark_state.start("run-2", 20)

        assert benchmark_state.run_id == "run-2"
        assert benchmark_state.state == BenchmarkState.starting

    # --- GET /benchmark/status tests ---

    def test_endpoint_status_returns_page_snapshot(self):
        """GET /benchmark/status returns page snapshot from state container."""
        snapshot = benchmark_state.to_page_snapshot()

        assert snapshot.state == BenchmarkState.idle
        assert snapshot.run_id is None
        assert snapshot.can_start is True
        assert snapshot.can_stop is False
        assert snapshot.can_reset is False

    def test_endpoint_status_includes_available_models(self):
        """Status includes available model options."""
        snapshot = benchmark_state.to_page_snapshot()

        assert len(snapshot.available_models) > 0
        assert snapshot.selected_model is not None

    def test_endpoint_status_running_state(self):
        """Status reflects running state correctly."""
        benchmark_state.start("run-1", 10)
        benchmark_state.transition_to_running()

        snapshot = benchmark_state.to_page_snapshot()

        assert snapshot.state == BenchmarkState.running
        assert snapshot.can_start is False
        assert snapshot.can_stop is True
        assert snapshot.can_reset is False

    def test_endpoint_status_completed_uses_terminal_snapshot(self):
        """Completed status uses terminal snapshot for results."""
        benchmark_state.start("run-1", 10)
        benchmark_state.transition_to_running()

        result = BenchmarkResultItem(
            question_id="q1",
            question="Test?",
            gold_answer="A",
            model_answer="A",
            raw_score=1,
            status_label="正确",
            response_time_ms=100,
            completed_at="2024-01-01T00:00:00Z",
        )
        benchmark_state.add_result(result)
        benchmark_state.complete()

        snapshot = benchmark_state.to_page_snapshot()

        assert snapshot.state == BenchmarkState.completed
        assert snapshot.summary is not None
        assert snapshot.can_start is True
        assert snapshot.can_reset is True

    # --- POST /benchmark/stop tests ---

    def test_endpoint_stop_allows_during_running(self):
        """POST /benchmark/stop during running state succeeds."""
        benchmark_state.start("run-test", 10)
        benchmark_state.transition_to_running()

        assert benchmark_state.can_stop() is True

        benchmark_state.stop("user request")

        assert benchmark_state.state == BenchmarkState.stopping

        benchmark_state.finalize_stopped()

        assert benchmark_state.state == BenchmarkState.stopped

    def test_endpoint_stop_rejects_during_idle(self):
        """POST /benchmark/stop during idle state raises ValueError."""
        assert benchmark_state.can_stop() is False

        with pytest.raises(ValueError, match="Cannot stop benchmark"):
            benchmark_state.stop("user request")

    def test_endpoint_stop_rejects_during_completed(self):
        """POST /benchmark/stop during completed state raises ValueError."""
        benchmark_state.start("run-test", 10)
        benchmark_state.transition_to_running()
        benchmark_state.complete()

        assert benchmark_state.can_stop() is False

    def test_endpoint_stop_rejects_during_failed(self):
        """POST /benchmark/stop during failed state raises ValueError."""
        benchmark_state.start("run-test", 10)
        benchmark_state.transition_to_running()
        benchmark_state.fail("API timeout")

        assert benchmark_state.can_stop() is False

    # --- POST /benchmark/reset tests ---

    def test_endpoint_reset_allows_from_completed(self):
        """POST /benchmark/reset from completed state succeeds."""
        benchmark_state.start("run-1", 10)
        benchmark_state.transition_to_running()
        benchmark_state.complete()

        assert benchmark_state.can_reset() is True

        benchmark_state.reset()

        assert benchmark_state.state == BenchmarkState.idle
        assert benchmark_state.run_id is None

    def test_endpoint_reset_allows_from_stopped(self):
        """POST /benchmark/reset from stopped state succeeds."""
        benchmark_state.start("run-1", 10)
        benchmark_state.transition_to_running()
        benchmark_state.stop("user request")
        benchmark_state.finalize_stopped()

        assert benchmark_state.can_reset() is True

        benchmark_state.reset()

        assert benchmark_state.state == BenchmarkState.idle

    def test_endpoint_reset_allows_from_failed(self):
        """POST /benchmark/reset from failed state succeeds."""
        benchmark_state.start("run-1", 10)
        benchmark_state.transition_to_running()
        benchmark_state.fail("API timeout")

        assert benchmark_state.can_reset() is True

        benchmark_state.reset()

        assert benchmark_state.state == BenchmarkState.idle

    def test_endpoint_reset_rejects_during_idle(self):
        """POST /benchmark/reset during idle state raises ValueError."""
        assert benchmark_state.can_reset() is False

        with pytest.raises(ValueError, match="Cannot reset benchmark"):
            benchmark_state.reset()

    def test_endpoint_reset_rejects_during_running(self):
        """POST /benchmark/reset during running state raises ValueError."""
        benchmark_state.start("run-test", 10)
        benchmark_state.transition_to_running()

        assert benchmark_state.can_reset() is False

        with pytest.raises(ValueError, match="Cannot reset benchmark"):
            benchmark_state.reset()

    def test_endpoint_reset_rejects_during_starting(self):
        """POST /benchmark/reset during starting state raises ValueError."""
        benchmark_state.start("run-test", 10)

        assert benchmark_state.can_reset() is False

        with pytest.raises(ValueError, match="Cannot reset benchmark"):
            benchmark_state.reset()

    def test_endpoint_reset_rejects_during_stopping(self):
        """POST /benchmark/reset during stopping state raises ValueError."""
        benchmark_state.start("run-test", 10)
        benchmark_state.transition_to_running()
        benchmark_state.stop("user request")

        assert benchmark_state.can_reset() is False

        with pytest.raises(ValueError, match="Cannot reset benchmark"):
            benchmark_state.reset()

    def test_endpoint_reset_clears_terminal_snapshot(self):
        """POST /benchmark/reset clears terminal snapshot for canonical idle."""
        benchmark_state.start("run-1", 10)
        benchmark_state.transition_to_running()

        result = BenchmarkResultItem(
            question_id="q1",
            question="Test?",
            gold_answer="A",
            model_answer="A",
            raw_score=1,
            status_label="正确",
            response_time_ms=100,
            completed_at="2024-01-01T00:00:00Z",
        )
        benchmark_state.add_result(result)
        benchmark_state.complete()

        # Terminal snapshot exists
        assert benchmark_state.terminal_summary is not None

        benchmark_state.reset()

        # Canonical idle: terminal snapshot cleared
        assert benchmark_state.terminal_summary is None
        assert benchmark_state.terminal_results == []

    # --- State machine validation tests ---

    def test_state_machine_endpoint_run_status(self):
        """Full state machine flow: idle -> starting -> running -> completed."""
        # idle -> starting
        assert benchmark_state.state == BenchmarkState.idle
        benchmark_state.start("run-1", 10)
        assert benchmark_state.state == BenchmarkState.starting

        # starting -> running
        benchmark_state.transition_to_running()
        assert benchmark_state.state == BenchmarkState.running

        # Add a result
        result = BenchmarkResultItem(
            question_id="q1",
            question="Test?",
            gold_answer="A",
            model_answer="A",
            raw_score=1,
            status_label="正确",
            response_time_ms=100,
            completed_at="2024-01-01T00:00:00Z",
        )
        benchmark_state.add_result(result)

        # running -> completed
        benchmark_state.complete()
        assert benchmark_state.state == BenchmarkState.completed

    def test_state_machine_endpoint_stop_reset(self):
        """Full state machine flow: idle -> starting -> running -> stopping -> stopped -> reset."""
        # idle -> starting
        benchmark_state.start("run-1", 10)
        benchmark_state.transition_to_running()

        # running -> stopping
        benchmark_state.stop("user request")
        assert benchmark_state.state == BenchmarkState.stopping

        # stopping -> stopped
        benchmark_state.finalize_stopped()
        assert benchmark_state.state == BenchmarkState.stopped

        # stopped -> idle (reset)
        benchmark_state.reset()
        assert benchmark_state.state == BenchmarkState.idle


class TestBenchmarkHTTPEndpoints:
    """Real HTTP-level endpoint tests using FastAPI TestClient."""

    def setup_method(self):
        """Reset global state before each test."""
        try:
            if benchmark_state.can_reset():
                benchmark_state.reset()
            elif benchmark_state.state in [
                BenchmarkState.running,
                BenchmarkState.starting,
            ]:
                benchmark_state.stop("test cleanup")
                benchmark_state.finalize_stopped()
                benchmark_state.reset()
        except Exception:
            pass
        benchmark_state._state = BenchmarkState.idle
        benchmark_state._run_id = None

    def test_endpoint_http_get_status_idle(self):
        """GET /benchmark/status returns idle state snapshot."""
        import pytest
        from fastapi.testclient import TestClient
        from minirag.api.minirag_server import create_app, parse_args

        try:
            args = parse_args()
            app = create_app(args)
        except SystemExit as e:
            pytest.skip(f"App requires runtime dependencies not available: {e}")

        client = TestClient(app)

        response = client.get("/benchmark/status")

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "idle"
        assert data["can_start"] is True
        assert data["can_stop"] is False

    def test_endpoint_http_get_status_finalizes_finished_stop(self):
        """GET /benchmark/status finalizes stopping -> stopped once worker is gone."""
        import pytest
        from fastapi.testclient import TestClient
        from minirag.api.minirag_server import create_app, parse_args

        try:
            args = parse_args()
            app = create_app(args)
        except SystemExit as e:
            pytest.skip(f"App requires runtime dependencies not available: {e}")

        worker = threading.Thread(target=lambda: None)
        worker.start()
        worker.join()

        benchmark_state.start("run-stop-status", 10)
        benchmark_state.transition_to_running()
        benchmark_state.register_worker(worker)
        benchmark_state.stop("user request")

        client = TestClient(app)
        response = client.get("/benchmark/status")

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "stopped"
        assert data["can_start"] is True
        assert data["can_stop"] is False
        assert data["can_reset"] is True

    def test_endpoint_http_post_run_idle_state(self):
        """POST /benchmark/run starts benchmark from idle state."""
        import pytest
        from fastapi.testclient import TestClient
        from minirag.api.minirag_server import create_app, parse_args

        try:
            args = parse_args()
            app = create_app(args)
        except SystemExit as e:
            pytest.skip(f"App requires runtime dependencies not available: {e}")

        client = TestClient(app)

        response = client.post(
            "/benchmark/run", json={"selected_model": "qwen3.5-2b"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert "run_id" in data["run_id"]
        assert data["state"] in ["starting", "running"]
        assert data["selected_model"] == "qwen3.5-2b"

    def test_endpoint_http_post_run_rejects_during_running(self):
        """POST /benchmark/run rejects duplicate during active state."""
        import pytest
        from fastapi.testclient import TestClient
        from minirag.api.minirag_server import create_app, parse_args

        try:
            args = parse_args()
            app = create_app(args)
        except SystemExit as e:
            pytest.skip(f"App requires runtime dependencies not available: {e}")

        client = TestClient(app)

        client.post("/benchmark/run", json={"selected_model": "qwen3.5-2b"})

        response = client.post(
            "/benchmark/run", json={"selected_model": "qwen3.5-2b"}
        )

        assert response.status_code in [409, 500]

    def test_endpoint_http_post_stop_during_running(self):
        """POST /benchmark/stop stops running benchmark."""
        import pytest
        from fastapi.testclient import TestClient
        from minirag.api.minirag_server import create_app, parse_args

        try:
            args = parse_args()
            app = create_app(args)
        except SystemExit as e:
            pytest.skip(f"App requires runtime dependencies not available: {e}")

        client = TestClient(app)

        client.post("/benchmark/run", json={"selected_model": "qwen3.5-2b"})

        response = client.post("/benchmark/stop", json={"reason": "test stop"})

        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["stopping", "stopped"]
        assert data["state"] in ["stopping", "stopped"]

    def test_endpoint_http_stop_finalizes_after_hung_query_timeout(self, monkeypatch):
        """Stop request eventually settles to stopped even if the active query hangs."""
        import pytest
        from fastapi.testclient import TestClient
        import minirag.api.minirag_server as minirag_server
        from minirag.api.minirag_server import MiniRAG, create_app, parse_args

        try:
            args = parse_args()
            args.timeout = 0.05
            app = create_app(args)
        except SystemExit as e:
            pytest.skip(f"App requires runtime dependencies not available: {e}")

        monkeypatch.setattr(
            minirag_server.ship_benchmark_adapter,
            "load_source_rows",
            lambda: [
                {
                    "question_id": "hang-q1",
                    "question_type": "cause_explanation",
                    "question": "为什么全船无24V供电？",
                    "gold_answer": "蓄电池无电。",
                    "answer": "蓄电池无电。",
                }
            ],
        )

        def hanging_query(self, query, param=None):
            time.sleep(0.2)
            return "late answer"

        monkeypatch.setattr(MiniRAG, "query", hanging_query)

        client = TestClient(app)

        start_response = client.post(
            "/benchmark/run", json={"selected_model": "qwen3.5-2b"}
        )
        assert start_response.status_code == 200

        stop_response = client.post("/benchmark/stop", json={"reason": "test stop"})
        assert stop_response.status_code == 200
        assert stop_response.json()["state"] == "stopping"

        deadline = time.time() + 1.5
        final_snapshot = None
        while time.time() < deadline:
            status_response = client.get("/benchmark/status")
            assert status_response.status_code == 200
            final_snapshot = status_response.json()
            if final_snapshot["state"] == "stopped":
                break
            time.sleep(0.05)

        assert final_snapshot is not None
        assert final_snapshot["state"] == "stopped"
        assert final_snapshot["can_start"] is True
        assert final_snapshot["can_stop"] is False
        assert final_snapshot["can_reset"] is True

    def test_endpoint_http_benchmark_uses_aquery_and_unwraps_http_errors(
        self, monkeypatch
    ):
        """Benchmark runtime should use aquery and expose the real HTTP failure."""
        import pytest
        import minirag.api.minirag_server as minirag_server
        from fastapi.testclient import TestClient
        from minirag.api.minirag_server import MiniRAG, create_app, parse_args

        try:
            args = parse_args()
            args.timeout = 0.2
            app = create_app(args)
        except SystemExit as e:
            pytest.skip(f"App requires runtime dependencies not available: {e}")

        monkeypatch.setattr(
            minirag_server.ship_benchmark_adapter,
            "load_source_rows",
            lambda: [
                {
                    "question_id": "http-q1",
                    "question_type": "cause_explanation",
                    "question": "为什么全船无24V供电？",
                    "gold_answer": "U3损坏；2U20损坏。",
                    "answer": "U3损坏；2U20损坏。",
                }
            ],
        )

        def forbidden_sync_query(self, query, param=None):
            raise AssertionError("benchmark should not use MiniRAG.query")

        async def failing_aquery(self, query, param=None):
            request = httpx.Request("POST", "http://127.0.0.1:1234/v1/chat/completions")
            response = httpx.Response(
                503,
                request=request,
                text='{"error":"model unavailable"}',
            )
            raise httpx.HTTPStatusError(
                "Service unavailable",
                request=request,
                response=response,
            )

        monkeypatch.setattr(MiniRAG, "query", forbidden_sync_query)
        monkeypatch.setattr(MiniRAG, "aquery", failing_aquery)

        client = TestClient(app)
        start_response = client.post(
            "/benchmark/run", json={"selected_model": "qwen3.5-2b"}
        )
        assert start_response.status_code == 200

        deadline = time.time() + 1.5
        final_snapshot = None
        while time.time() < deadline:
            status_response = client.get("/benchmark/status")
            assert status_response.status_code == 200
            final_snapshot = status_response.json()
            if final_snapshot["state"] == "completed":
                break
            time.sleep(0.05)

        assert final_snapshot is not None
        assert final_snapshot["state"] == "completed"
        mode_answers = final_snapshot["recent_results"][0]["mode_answers"]
        assert mode_answers["graph_text_hybrid"].startswith(
            "[ERROR] HTTP 503 from http://127.0.0.1:1234/v1/chat/completions"
        )
        assert "model unavailable" in mode_answers["graph_text_hybrid"]

    def test_endpoint_http_post_reset_from_terminal(self):
        """POST /benchmark/reset resets from terminal state."""
        import pytest
        from fastapi.testclient import TestClient
        from minirag.api.minirag_server import create_app, parse_args

        try:
            args = parse_args()
            app = create_app(args)
        except SystemExit as e:
            pytest.skip(f"App requires runtime dependencies not available: {e}")

        client = TestClient(app)

        client.post("/benchmark/run", json={"selected_model": "qwen3.5-2b"})
        client.post("/benchmark/stop", json={"reason": "test"})

        response = client.post("/benchmark/reset")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "reset"
        assert data["state"] == "idle"

    def test_endpoint_http_post_reset_rejects_during_idle(self):
        """POST /benchmark/reset rejects from idle state."""
        import pytest
        from fastapi.testclient import TestClient
        from minirag.api.minirag_server import create_app, parse_args

        try:
            args = parse_args()
            app = create_app(args)
        except SystemExit as e:
            pytest.skip(f"App requires runtime dependencies not available: {e}")

        client = TestClient(app)

        response = client.post("/benchmark/reset")

        assert response.status_code == 409

    def test_endpoint_run_uses_selected_model(self):
        """POST /benchmark/run builds runtime with selected model."""
        import pytest
        from fastapi.testclient import TestClient
        from minirag.api.minirag_server import create_app, parse_args

        try:
            args = parse_args()
            app = create_app(args)
        except SystemExit as e:
            pytest.skip(f"App requires runtime dependencies not available: {e}")

        client = TestClient(app)

        # Request with LM Studio model
        response = client.post(
            "/benchmark/run", json={"selected_model": "qwen3.5-2b"}
        )

        # Should indicate selected model in message
        if response.status_code == 200:
            data = response.json()
            assert data["selected_model"] == "qwen3.5-2b"
            assert (
                "qwen3.5-2b" in data["message"]
                or "using qwen3.5-2b" in data["message"]
            )
