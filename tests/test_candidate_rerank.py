# ruff: noqa: E402
import asyncio
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import minirag.candidate_rerank as candidate_rerank
from minirag.base import QueryParam


def test_rerank_disabled_passthrough():
    candidates = [
        {"id": "a", "content": "alpha", "score": 10.0},
        {"id": "b", "content": "beta", "score": 9.0},
    ]
    result = asyncio.run(
        candidate_rerank.rerank_faultcase_candidates(
            "query",
            candidates,
            QueryParam(faultcase_rerank_enabled=False),
            {},
        )
    )
    assert result == candidates


def test_rerank_failure_passthrough():
    original = candidate_rerank._local_rerank_sync

    def failing(*args, **kwargs):
        raise RuntimeError("boom")

    candidate_rerank._local_rerank_sync = failing
    try:
        candidates = [
            {"id": "a", "content": "alpha", "score": 10.0},
            {"id": "b", "content": "beta", "score": 9.0},
        ]
        result = asyncio.run(
            candidate_rerank.rerank_faultcase_candidates(
                "query",
                candidates,
                QueryParam(
                    faultcase_rerank_enabled=True,
                    faultcase_rerank_max_candidates=2,
                    faultcase_rerank_timeout_ms=100,
                ),
                {"faultcase_rerank_model": "demo-model"},
            )
        )
        assert result == candidates
    finally:
        candidate_rerank._local_rerank_sync = original


def test_rerank_limits_small_candidate_set():
    original = candidate_rerank._local_rerank_sync

    def fake_local(query, candidates, model, device, precision):
        del query, model, device, precision
        return list(reversed(candidates))

    candidate_rerank._local_rerank_sync = fake_local
    try:
        candidates = [
            {"id": "a", "content": "alpha", "score": 10.0},
            {"id": "b", "content": "beta", "score": 9.0},
            {"id": "c", "content": "gamma", "score": 8.0},
        ]
        result = asyncio.run(
            candidate_rerank.rerank_faultcase_candidates(
                "query",
                candidates,
                QueryParam(
                    faultcase_rerank_enabled=True,
                    faultcase_rerank_max_candidates=2,
                    faultcase_rerank_timeout_ms=100,
                ),
                {"faultcase_rerank_model": "demo-model"},
            )
        )
        assert [item["id"] for item in result] == ["b", "a", "c"]
    finally:
        candidate_rerank._local_rerank_sync = original


def test_local_rerank_sync_uses_huggingface_tuple_fallback():
    original = candidate_rerank._load_local_reranker
    original_torch = sys.modules.get("torch")

    class FakeTensor:
        def __init__(self):
            self.moves = []

        def to(self, device):
            self.moves.append(device)
            return self

    class FakeTokenizer:
        def __init__(self):
            self.calls = []
            self.input_ids = FakeTensor()
            self.attention_mask = FakeTensor()

        def __call__(self, pairs, **kwargs):
            self.calls.append((pairs, kwargs))
            return {
                "input_ids": self.input_ids,
                "attention_mask": self.attention_mask,
            }

    class FakeLogits:
        def __init__(self, scores):
            self.scores = scores

        def view(self, *_args):
            return self

        def float(self):
            return self

        def tolist(self):
            return list(self.scores)

    class FakeModel:
        def __init__(self):
            self.calls = []

        def __call__(self, **kwargs):
            self.calls.append(kwargs)
            return types.SimpleNamespace(logits=FakeLogits([0.1, 0.9, 0.4]))

    class _NoGrad:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    tokenizer = FakeTokenizer()
    model = FakeModel()
    fake_torch = types.SimpleNamespace(no_grad=lambda: _NoGrad())

    def fake_load(model_name, device, precision):
        assert model_name == "demo-model"
        assert device == "cpu"
        assert precision == "fp32"
        return tokenizer, model, "cpu"

    candidate_rerank._load_local_reranker = fake_load
    sys.modules["torch"] = fake_torch

    try:
        candidates = [
            {"id": "a", "content": "alpha"},
            {"id": "b", "description": "beta"},
            {"id": "c", "entity_name": "gamma"},
        ]
        result = candidate_rerank._local_rerank_sync(
            "query",
            candidates,
            "demo-model",
            "cpu",
            "fp32",
        )

        assert [item["id"] for item in result] == ["b", "c", "a"]
        assert [item["rerank_score"] for item in result] == [0.9, 0.4, 0.1]
        assert all(item["reranked"] for item in result)

        pairs, kwargs = tokenizer.calls[0]
        assert pairs == [("query", "alpha"), ("query", "beta"), ("query", "gamma")]
        assert kwargs["padding"] is True
        assert kwargs["truncation"] is True
        assert kwargs["return_tensors"] == "pt"
        assert kwargs["max_length"] == 512
        assert tokenizer.input_ids.moves == ["cpu"]
        assert tokenizer.attention_mask.moves == ["cpu"]
        assert model.calls[0]["return_dict"] is True
    finally:
        candidate_rerank._load_local_reranker = original
        if original_torch is None:
            sys.modules.pop("torch", None)
        else:
            sys.modules["torch"] = original_torch
