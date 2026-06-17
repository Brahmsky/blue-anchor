import argparse
import csv
import json
import os
import sys
import time
import importlib.util
import types
import subprocess
import threading
from pathlib import Path
from typing import Any
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import json_repair
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


RUNTIME_BASE_REF = "291fb35"


def _git_show(path: str, ref: str = "HEAD") -> str:
    return subprocess.check_output(
        ["git", "show", f"{ref}:{path}"], cwd=ROOT, text=True, encoding="utf-8"
    )


def _exec_module_from_source(
    module_name: str, source: str, file_path: Path, package: str | None = None
) -> types.ModuleType:
    module = types.ModuleType(module_name)
    module.__file__ = str(file_path)
    module.__package__ = package or module_name.rpartition(".")[0]
    exec(compile(source, str(file_path), "exec"), module.__dict__)
    sys.modules[module_name] = module
    return module


def bootstrap_local_minirag() -> None:
    if "minirag.api.minirag_server" in sys.modules:
        return

    package_root = ROOT / "minirag"

    minirag_pkg = types.ModuleType("minirag")
    minirag_pkg.__path__ = [str(package_root)]
    minirag_pkg.__file__ = str(package_root / "__init__.py")
    minirag_pkg.__package__ = "minirag"
    minirag_pkg.__version__ = "0.0.2"
    sys.modules["minirag"] = minirag_pkg

    api_pkg = types.ModuleType("minirag.api")
    api_pkg.__path__ = [str(package_root / "api")]
    api_pkg.__file__ = str(package_root / "api" / "__init__.py")
    api_pkg.__package__ = "minirag.api"
    api_pkg.__api_version__ = "1.0.3"
    sys.modules["minirag.api"] = api_pkg

    _exec_module_from_source(
        "minirag.utils",
        _git_show("minirag/utils.py", ref=RUNTIME_BASE_REF),
        package_root / "utils.py",
        "minirag",
    )
    _exec_module_from_source(
        "minirag.base",
        _git_show("minirag/base.py", ref=RUNTIME_BASE_REF),
        package_root / "base.py",
        "minirag",
    )
    minirag_spec = importlib.util.spec_from_file_location(
        "minirag.minirag",
        package_root / "minirag.py",
    )
    if minirag_spec is None or minirag_spec.loader is None:
        raise RuntimeError("Unable to load minirag.minirag")
    minirag_module = importlib.util.module_from_spec(minirag_spec)
    sys.modules["minirag.minirag"] = minirag_module
    minirag_spec.loader.exec_module(minirag_module)
    minirag_pkg.MiniRAG = minirag_module.MiniRAG
    minirag_pkg.QueryParam = sys.modules["minirag.base"].QueryParam
    minirag_pkg.__api_version__ = "1.0.3"

    server_spec = importlib.util.spec_from_file_location(
        "minirag.api.minirag_server",
        package_root / "api" / "minirag_server.py",
        submodule_search_locations=[str(package_root / "api")],
    )
    if server_spec is None or server_spec.loader is None:
        raise RuntimeError("Unable to load minirag.api.minirag_server")
    server_module = importlib.util.module_from_spec(server_spec)
    sys.modules["minirag.api.minirag_server"] = server_module
    server_spec.loader.exec_module(server_module)


bootstrap_local_minirag()
from minirag.api.minirag_server import create_app, parse_args  # type: ignore # noqa: E402


DEFAULT_QA_CSV = (
    ROOT / "datasources" / "local_ship_docs" / "outputs" / "benchmark" / "query_set.csv"
)
DEFAULT_OUTPUT_CSV = (
    ROOT
    / "datasources"
    / "local_ship_docs"
    / "outputs"
    / "benchmark"
    / "query_set_judged.csv"
)
DEFAULT_SUMMARY_JSON = (
    ROOT
    / "datasources"
    / "local_ship_docs"
    / "outputs"
    / "benchmark"
    / "query_set_judged_summary.json"
)

ANSWER_COLUMNS = {
    "graph_text_hybrid": "graph_text_hybrid_plain",
    "graph_only": "graph_only_plain",
    "text_only": "text_only_plain",
}


class LocalRerankService:
    def __init__(self, model_name: str, host: str = "127.0.0.1", port: int = 8765):
        self.model_name = model_name
        self.host = host
        self.port = port
        self._server = None
        self._thread = None
        self._tokenizer = None
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return
        from transformers import AutoTokenizer, BertForSequenceClassification
        import torch

        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self._model = BertForSequenceClassification.from_pretrained(self.model_name)
        self._model.to("cpu")
        self._model.eval()
        self._torch = torch

    def start(self):
        self._load_model()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    query = payload.get("query", "")
                    documents = payload.get("documents", []) or []
                    top_k = int(payload.get("top_k", len(documents)))

                    pairs = [(query, doc) for doc in documents]
                    inputs = owner._tokenizer(
                        pairs,
                        padding=True,
                        truncation=True,
                        return_tensors="pt",
                        max_length=512,
                    )
                    with owner._torch.no_grad():
                        scores = (
                            owner._model(**inputs, return_dict=True)
                            .logits.view(-1)
                            .float()
                            .tolist()
                        )
                    ranked = sorted(
                        range(len(scores)), key=lambda idx: scores[idx], reverse=True
                    )[:top_k]
                    body = json.dumps(
                        {
                            "results": [
                                {"index": idx, "score": scores[idx]} for idx in ranked
                            ]
                        },
                        ensure_ascii=False,
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as exc:  # noqa: BLE001
                    body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode(
                        "utf-8"
                    )
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A003
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


def build_args() -> argparse.Namespace:
    original_argv = sys.argv[:]
    try:
        sys.argv = [original_argv[0]]
        args = parse_args()
        args.graph_storage = "NetworkXStorage"
        return args
    finally:
        sys.argv = original_argv


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_existing_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def build_prompt(
    question: str,
    gold_answer: str,
    evidence: str,
    question_type: str,
    answers: dict[str, str],
) -> str:
    lines = [
        "Now, I will give you a question, a gold answer, optional evidence, and several answers produced by different RAG systems.",
        "",
        "Scoring rules:",
        "- If the answer is correct and matches the gold answer, score 1.",
        "- If the answer is irrelevant, evasive, or only says there is no information when the gold answer is answerable, score 0.",
        "- If the answer is materially wrong, score -1.",
        "",
        "Please return only one JSON object.",
        "Use the exact system names below as JSON keys.",
        "",
        "Question:",
        question,
        "",
        "Gold Answer:",
        gold_answer,
        "",
        "Evidence:",
        evidence,
        "",
        "Type:",
        question_type,
        "",
    ]
    for key, value in answers.items():
        lines.append(f"{key}: {value}")
    lines.extend(
        [
            "",
            "Expected JSON format:",
            json.dumps({key: 1 for key in answers}, ensure_ascii=False, indent=2),
        ]
    )
    return "\n".join(lines).strip()


def judge_answers(
    question: str,
    gold_answer: str,
    evidence: str,
    question_type: str,
    answers: dict[str, str],
) -> dict[str, int]:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    prompt = build_prompt(question, gold_answer, evidence, question_type, answers)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "http://localhost"),
        "X-OpenRouter-Title": os.getenv("OPENROUTER_TITLE", "GraphRAG Benchmark Judge"),
    }
    payload = {
        "model": "nvidia/nemotron-3-super-120b-a12b:free",
        "messages": [
            {"role": "system", "content": "You are a strict benchmark evaluator."},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            with httpx.Client(timeout=180) as client:
                response = client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
            if not isinstance(data, dict) or not data.get("choices"):
                raise RuntimeError(
                    f"Judge payload missing choices: {json.dumps(data, ensure_ascii=False)[:500]}"
                )
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == 5:
                raise
            time.sleep(4 * attempt)
    else:
        raise RuntimeError(f"Judge request failed: {last_error}")
    content = data["choices"][0]["message"]["content"]
    parsed = json_repair.loads(content)
    if isinstance(parsed, str):
        parsed = json_repair.loads(parsed) if parsed.strip() else {}
    if not isinstance(parsed, dict):
        parsed = {}
    judged: dict[str, int] = {}
    for key in answers:
        value = parsed.get(key, 0)
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            normalized = 0
        if normalized not in {-1, 0, 1}:
            normalized = 0
        judged[key] = normalized
    return judged


def query_one(client: TestClient, mode: str, question: str) -> tuple[str, float]:
    started = time.perf_counter()
    response = client.post(
        "/query/plain",
        json={
            "query": question,
            "mode": mode,
            "stream": False,
            "only_need_context": False,
        },
        timeout=300,
    )
    elapsed = time.perf_counter() - started
    response.raise_for_status()
    return response.text.strip(), elapsed


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark graph_text_hybrid / graph_only / text_only on the latest ship QA set.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--qa-csv",
        default=str(DEFAULT_QA_CSV),
        help="Path to the canonical benchmark QA input CSV.",
    )
    parser.add_argument(
        "--output-csv",
        default=str(DEFAULT_OUTPUT_CSV),
        help="Path to the judged benchmark CSV output.",
    )
    parser.add_argument(
        "--summary-json",
        default=str(DEFAULT_SUMMARY_JSON),
        help="Path to the judged benchmark summary JSON output.",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Optional row limit for smoke runs"
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from existing output csv if present",
    )
    parser.add_argument(
        "--rerank-model",
        default="",
        help="Optional local cross-encoder rerank model to expose over a local HTTP bridge",
    )
    parser.add_argument("--rerank-port", type=int, default=8765)
    args = parser.parse_args()

    qa_path = Path(args.qa_csv)
    output_csv = Path(args.output_csv)
    summary_json = Path(args.summary_json)
    rows = load_rows(qa_path)
    if args.limit > 0:
        rows = rows[: args.limit]

    rerank_service = None
    if args.rerank_model:
        rerank_service = LocalRerankService(args.rerank_model, port=args.rerank_port)
        rerank_service.start()
        os.environ["FAULTCASE_RERANK_ENABLED"] = "true"
        os.environ["FAULTCASE_RERANK_BASE_URL"] = f"http://127.0.0.1:{args.rerank_port}"
        os.environ["FAULTCASE_RERANK_MODEL"] = ""

    app = create_app(build_args())
    client = TestClient(app)

    output_rows: list[dict[str, Any]] = []
    processed_questions: set[str] = set()
    try:
        if args.resume:
            existing_rows = load_existing_rows(output_csv)
            output_rows.extend(existing_rows)
            processed_questions = {row.get("Question", "") for row in existing_rows}
        summaries = {
            mode: {"1": 0, "0": 0, "-1": 0, "accuracy_percent": 0.0}
            for mode in ANSWER_COLUMNS
        }

        for existing in output_rows:
            for mode, answer_col in ANSWER_COLUMNS.items():
                raw = existing.get(f"judge_{answer_col}")
                if raw in {"1", "0", "-1", 1, 0, -1}:
                    summaries[mode][str(int(raw))] += 1

        for idx, row in enumerate(rows, start=1):
            question = row.get("Question", "")
            if question in processed_questions:
                print(f"[{idx}/{len(rows)}] skip")
                continue
            gold_answer = row.get("Gold Answer", "")
            evidence = row.get("Evidence", "")
            question_type = row.get("Type", "")

            answers: dict[str, str] = {}
            timings: dict[str, float] = {}

            for mode, answer_col in ANSWER_COLUMNS.items():
                try:
                    answer, elapsed = query_one(client, mode, question)
                except Exception as exc:
                    answer = f"[ERROR] {exc}"
                    elapsed = -1.0
                answers[answer_col] = answer
                timings[f"{mode}_seconds"] = round(elapsed, 4)

            judged = judge_answers(
                question, gold_answer, evidence, question_type, answers
            )

            output_row = {
                "Question": question,
                "Gold Answer": gold_answer,
                "Evidence": evidence,
                "Type": question_type,
                **answers,
                **timings,
            }

            for mode, answer_col in ANSWER_COLUMNS.items():
                score = judged[answer_col]
                output_row[f"judge_{answer_col}"] = score
                summaries[mode][str(score)] += 1

            output_rows.append(output_row)
            write_csv(output_csv, output_rows)
            print(f"[{idx}/{len(rows)}] done")

        total = len(output_rows)
        for mode, answer_col in ANSWER_COLUMNS.items():
            summaries[mode]["accuracy_percent"] = (
                round(summaries[mode]["1"] / total * 100, 2) if total else 0.0
            )
            summaries[mode]["answer_column"] = answer_col

        summary_json.write_text(
            json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
    finally:
        if rerank_service is not None:
            rerank_service.stop()


if __name__ == "__main__":
    main()
