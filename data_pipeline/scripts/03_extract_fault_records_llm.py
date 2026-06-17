import argparse
import importlib
import json
import os
import re
import sys
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.script_runtime import (
    add_datasource_arguments,
    discover_doc_dirs as discover_doc_dirs_under_root,
    resolve_chunks_root,
    resolve_doc_dir_argument,
)


DEFAULT_MODEL = "stepfun/step-3.5-flash:free"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_INPUT_FOLDER = "good_chunks"
DEFAULT_ACCEPTED_FOLDER = "accepted_records"
DEFAULT_REJECTED_FOLDER = "rejected_chunks"
DEFAULT_REPORT_NAME = "llm_extract_report.json"
DEFAULT_REQUESTS_PER_MINUTE = 18
DEFAULT_MAX_CONCURRENCY = 6


def _load_optional_module(module_name: str):
    try:
        return importlib.import_module(module_name)
    except ImportError:
        return None


def _load_required_module(module_name: str):
    module = _load_optional_module(module_name)
    if module is None:
        raise RuntimeError(f"Required dependency is not installed: {module_name}")
    return module


dotenv_module = _load_optional_module("dotenv")
if dotenv_module is not None and hasattr(dotenv_module, "load_dotenv"):
    dotenv_module.load_dotenv(override=False)

SYSTEM_PROMPT = """你是船舶装备故障知识库的数据工程助手。

你的默认任务是：把输入的 chunk 抽取成一条“故障卡片”结构化记录。
但如果输入信息不足以形成一条故障卡片，你必须拒绝抽取，并返回 reject 结果。

你不是在抽知识图谱节点，也不是在写总结，而是在填一张固定字段的表。

【优先目标】
1. 尽量抽出一条完整的故障卡片
2. 如果无法形成故障卡片，再拒绝
3. 不要因为文本里有少量背景句就轻易拒绝
4. 只要能明确抽出故障现象、故障原因、处理步骤、注意事项、可能后果中的一部分，并且主体是某种装备或系统的故障，就优先输出 ok

【应当拒绝的情况】
1. 纯教学、纯综述、纯背景介绍
2. 只讲原理，不讲具体故障现象或处理
3. 只讲设备结构、组成、用途，没有故障上下文
4. 纯表格说明、纯图片引用、目录、标题残片
5. 信息过于零散，无法形成一条故障卡片

【强约束】
1. 只抽取一条记录
2. 尽量贴近原文措辞，不要自由发挥
3. 如果某个字段原文没有明确提到，就输出空数组或空字符串
4. 不要根据常识补充内容
5. actions 必须保留顺序
6. consequences 只有原文明确提到后果、影响、结果时才填
7. precautions 只有原文明确提到注意事项、禁忌、风险提醒时才填
8. key_components 只保留与该故障直接相关的关键部件
9. equipment 可以结合 breadcrumb、标题、正文一起判断，但必须优先服从标题和 breadcrumb 中的装备对象，不要被正文里的局部部件带偏
10. fault 应该尽量贴近原文里的故障卡片标题或故障现象，不要改写成宽泛主题
11. source_text 保留原始正文，不要改写
12. 如果 chunk 明显属于同一装备的故障条目，即使正文前半段偏现象、后半段偏原因，也应优先尝试抽取，不要轻易 reject

【equipment 字段要求】
- 优先使用 breadcrumb、标题、正文里最具体的装备名
- 如果标题或 breadcrumb 已经给出了明确装备对象，优先采用那个对象，不要改成正文里出现的更细部件名
- 例如标题是“操舵故障”，equipment 应优先写“操舵装置”，不要写成“液压舵机”
- 例如标题是“驾驶台供电故障”，equipment 应优先写“驾驶台供电系统”，不要只写“主配电板”
- 不要把“主辅机供油系统”缩写成“主辅机”
- 不要把“24V供电系统”缩写成“24V供电”或“供电”
- 不要把“主辅机冷却水供水系统”缩写成“冷却系统”

【fault 字段要求】
- 优先贴近原文中的故障现象或条目名
- 不要凭空总结成更泛的“XX故障”
- 如果标题只是泛称“XX故障”，而正文给出了更具体的故障现象，优先用正文里的具体故障现象
- 例如原文写“主辅机无燃油供应或供油不畅”，就不要改写成“主辅机供油故障”
- 例如原文写“雨刮器不动作”，就不要改写成“雨刮器故障”

【consequences 字段要求】
- 只有原文明确说“会导致”“将会出现”“后果是”“最终会造成”时才填写
- 一旦原文明确写了后果，就必须提取，不要漏掉
- 例如“电瓶出故障将会出现无法充电或无法储存电能”，应提取到 consequences

【输出要求】
只输出一个 JSON 对象，不要输出任何解释性文字，不要使用 markdown 代码块。

JSON 格式必须是：
{
  "status": "ok" 或 "reject",
  "equipment": "",
  "fault": "",
  "symptom": "",
  "causes": [],
  "actions": [],
  "consequences": [],
  "precautions": [],
  "key_components": [],
  "source_text": "",
  "rejection_reason": "",
  "extraction_notes": ""
}
"""

USER_PROMPT_TEMPLATE = """请基于下面的 chunk 进行“抽取或拒绝”。

[doc_name]
{doc_name}

[chapter]
{chapter}

[chapter_file]
{chapter_file}

[breadcrumb]
{breadcrumb}

[record_title]
{record_title}

[equipment_hint]
{equipment_hint}

[chunk_type]
{chunk_type}

[content]
{content}

请模仿下面这些“抽取原则示例”的判断方式和字段风格，但不要机械照抄示例里的名词。

【few-shot 1: 应该抽取】
输入片段：
故障现象：设备无法正常动作。故障原因：电源未打开，执行机构松动。处理方法：检查电源，紧固松动部件，调整到正确位置后重新试车。需要注意的是，空载异常工况下严禁强行运行。

输出示例：
{{
  "status": "ok",
  "equipment": "设备名称",
  "fault": "设备无法正常动作",
  "symptom": "设备无法正常动作。",
  "causes": ["电源未打开", "执行机构松动"],
  "actions": ["检查电源", "紧固松动部件", "调整到正确位置后重新试车"],
  "consequences": [],
  "precautions": ["空载异常工况下严禁强行运行"],
  "key_components": ["执行机构", "连接部件"],
  "source_text": "原文略",
  "rejection_reason": "",
  "extraction_notes": "原文未明确提及后果。"
}}

【few-shot 2: 应该抽取 consequences】
输入片段：
故障现象：系统无法启动。故障原因：储能单元失效。处理方法：检查控制部分，检查驱动来源，检查储能单元状态，必要时更换。储能单元故障将会出现无法充能或无法储能。

输出示例：
{{
  "status": "ok",
  "equipment": "系统名称",
  "fault": "系统无法启动",
  "symptom": "系统无法启动。",
  "causes": ["储能单元失效"],
  "actions": ["检查控制部分", "检查驱动来源", "检查储能单元状态", "必要时更换"],
  "consequences": ["无法充能", "无法储能"],
  "precautions": [],
  "key_components": ["控制部分", "驱动来源", "储能单元"],
  "source_text": "原文略",
  "rejection_reason": "",
  "extraction_notes": "原文未明确提及注意事项。"
}}

【few-shot 3: 应该拒绝】
输入片段：
柴油机是船舶动力系统中最常用的发动机类型之一，其主要由气缸、曲轴、燃油系统、润滑系统和冷却系统组成。

输出示例：
{{
  "status": "reject",
  "equipment": "",
  "fault": "",
  "symptom": "",
  "causes": [],
  "actions": [],
  "consequences": [],
  "precautions": [],
  "key_components": [],
  "source_text": "原文略",
  "rejection_reason": "该块主要是设备结构和组成介绍，没有形成明确故障卡片。",
  "extraction_notes": "不属于故障条目。"
}}
"""


class RequestRateLimiter:
    def __init__(self, requests_per_minute: int):
        self.requests_per_minute = max(1, requests_per_minute)
        self.window_seconds = 60.0
        self.started_at: deque[float] = deque()
        self.lock = threading.Lock()

    def wait_for_slot(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                while (
                    self.started_at and now - self.started_at[0] >= self.window_seconds
                ):
                    self.started_at.popleft()
                if len(self.started_at) < self.requests_per_minute:
                    self.started_at.append(now)
                    return
                sleep_for = self.window_seconds - (now - self.started_at[0])
            time.sleep(max(0.05, sleep_for))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract fault records from datasource staging/chunks good_chunks with OpenRouter LLM; reject chunks that cannot form a fault card."
    )
    add_datasource_arguments(parser)
    parser.add_argument(
        "--doc-dir",
        default=None,
        help="Path or name of one datasource document directory under staging/chunks.",
    )
    parser.add_argument(
        "--all-docs",
        action="store_true",
        help="Run for every datasource document folder under staging/chunks that contains good_chunks.",
    )
    parser.add_argument(
        "--input-folder",
        default=DEFAULT_INPUT_FOLDER,
        help="Folder name under each doc dir that contains candidate chunk json files.",
    )
    parser.add_argument(
        "--accepted-folder",
        default=DEFAULT_ACCEPTED_FOLDER,
        help="Folder name under each doc dir to store accepted extracted records.",
    )
    parser.add_argument(
        "--rejected-folder",
        default=DEFAULT_REJECTED_FOLDER,
        help="Folder name under each doc dir to store rejected chunks.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenRouter model name.")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="OpenRouter API base URL (e.g., https://openrouter.ai/api/v1).",
    )
    parser.add_argument(
        "--report-name",
        default=DEFAULT_REPORT_NAME,
        help="Per-doc extraction report file name.",
    )
    parser.add_argument(
        "--temperature", type=float, default=0, help="Sampling temperature."
    )
    parser.add_argument(
        "--max-retries", type=int, default=3, help="Max retries for each chunk request."
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=2.0,
        help="Seconds to sleep between retries.",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=DEFAULT_MAX_CONCURRENCY,
        help="Maximum number of concurrent chunk extraction workers.",
    )
    parser.add_argument(
        "--requests-per-minute",
        type=int,
        default=DEFAULT_REQUESTS_PER_MINUTE,
        help="Request start rate limit. Default uses a conservative value below the documented 20 RPM free-tier ceiling.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print detailed per-chunk logs."
    )
    parser.add_argument(
        "--write-report",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Whether to write per-doc llm_extract_report.json.",
    )
    parser.add_argument(
        "--write-root-summary",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Whether to write outputs-root/llm_extract_summary.json.",
    )
    return parser


def ensure_api_key() -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set in the current environment")
    return api_key


def discover_doc_dirs(chunks_root: Path, input_folder: str) -> list[Path]:
    return discover_doc_dirs_under_root(chunks_root, input_folder)


def resolve_doc_dirs(args: argparse.Namespace) -> list[Path]:
    chunks_root = resolve_chunks_root(args)
    if args.all_docs or not args.doc_dir:
        return discover_doc_dirs(chunks_root, args.input_folder)
    path = resolve_doc_dir_argument(args.doc_dir, chunks_root)
    if not path.exists():
        raise FileNotFoundError(f"Document directory does not exist: {path}")
    return [path]


def reset_output_dir(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for child in output_dir.iterdir():
        if child.is_file():
            child.unlink()


def load_payload(json_file: Path) -> dict[str, Any]:
    return json.loads(json_file.read_text(encoding="utf-8"))


def extract_record_title(breadcrumb: str) -> str:
    parts = [
        part.strip()
        for part in str(breadcrumb or "").replace(".md", "").split(">")
        if part.strip()
    ]
    return parts[-1] if parts else ""


def derive_equipment_hint(record_title: str) -> str:
    title = str(record_title or "").strip()
    title = re.sub(r"^\d+(\.\d+)*\s*", "", title)
    title = title.replace("主、辅机", "主辅机")
    for suffix in ["常见问题", "故障", "异常", "问题"]:
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
            break
    title = title.replace("系统供水", "供水系统")
    title = title.replace("系统供油", "供油系统")
    title = title.replace("系统供电", "供电系统")
    if any(
        title.endswith(suffix) for suffix in ["供水", "供油", "供电"]
    ) and not title.endswith("系统"):
        title = title + "系统"
    return title.strip()


def build_user_prompt(payload: dict[str, Any]) -> str:
    chunk = payload.get("chunk", payload)
    record_title = extract_record_title(chunk.get("breadcrumb", ""))
    equipment_hint = derive_equipment_hint(record_title)
    return USER_PROMPT_TEMPLATE.format(
        doc_name=payload.get("doc_name", ""),
        chapter=payload.get("chapter", ""),
        chapter_file=chunk.get("chapter_file", ""),
        breadcrumb=chunk.get("breadcrumb", ""),
        record_title=record_title,
        equipment_hint=equipment_hint,
        chunk_type=chunk.get("chunk_type", ""),
        content=chunk.get("content", ""),
    )


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    json_repair_module = _load_optional_module("json_repair")
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            if json_repair_module is not None:
                return json_repair_module.loads(cleaned)
        except Exception:
            pass
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            sliced = cleaned[start : end + 1]
            try:
                return json.loads(sliced)
            except json.JSONDecodeError:
                if json_repair_module is None:
                    raise
                return json_repair_module.loads(sliced)
        raise


def normalize_record(data: dict[str, Any], source_text: str) -> dict[str, Any]:
    def ensure_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    status = str(data.get("status", "")).strip().lower()
    if status not in {"ok", "reject"}:
        status = "reject"

    return {
        "status": status,
        "equipment": str(data.get("equipment", "") or "").strip(),
        "fault": str(data.get("fault", "") or "").strip(),
        "symptom": str(data.get("symptom", "") or "").strip(),
        "causes": ensure_list(data.get("causes")),
        "actions": ensure_list(data.get("actions")),
        "consequences": ensure_list(data.get("consequences")),
        "precautions": ensure_list(data.get("precautions")),
        "key_components": ensure_list(data.get("key_components")),
        "source_text": str(data.get("source_text", "") or source_text).strip(),
        "rejection_reason": str(data.get("rejection_reason", "") or "").strip(),
        "extraction_notes": str(data.get("extraction_notes", "") or "").strip(),
    }


def postprocess_record(normalized: dict[str, Any], breadcrumb: str) -> dict[str, Any]:
    record_title = extract_record_title(breadcrumb)
    equipment_hint = derive_equipment_hint(record_title)
    equipment = str(normalized.get("equipment", "") or "").strip()
    source_text = str(normalized.get("source_text", "") or "")

    equipment = equipment.replace("、", "")
    equipment = equipment.replace("系统供水系统", "供水系统")
    equipment = equipment.replace("系统供油系统", "供油系统")
    equipment = equipment.replace("系统供电系统", "供电系统")

    if equipment == "舱口盖" and "平式舱口盖" in source_text:
        equipment = "平式舱口盖"
    if equipment == "门窗" and "门窗边框" in breadcrumb:
        equipment = "门窗边框"

    normalized["equipment"] = equipment
    if equipment_hint:
        if not equipment:
            normalized["equipment"] = equipment_hint
        else:
            compact_equipment = equipment.replace("、", "")
            compact_hint = equipment_hint.replace("、", "")
            if compact_equipment in compact_hint and len(compact_equipment) < len(
                compact_hint
            ):
                normalized["equipment"] = equipment_hint
    return normalized


def call_openrouter(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_retries: int,
    retry_delay: float,
    verbose: bool = False,
    chunk_label: str = "",
    stop_event: threading.Event | None = None,
) -> str:
    openai_module = _load_required_module("openai")
    client = openai_module.OpenAI(
        api_key=api_key,
        base_url=base_url,
        default_headers={
            "HTTP-Referer": "https://github.com/8G3S/GraphRAG-benchmark",
            "X-OpenRouter-Title": "GraphRAG-Benchmark",
        },
    )

    last_error = None
    for attempt in range(1, max_retries + 1):
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("document processing cancelled")
        try:
            if verbose:
                print(
                    f"[llm] request chunk={chunk_label} attempt={attempt} model={model}"
                )
            response = client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                timeout=120,
            )
            content = response.choices[0].message.content or ""
            if verbose:
                preview = content[:80].replace("\n", " ")
                print(f"[llm] response chunk={chunk_label} preview={preview}")
            return content
        except Exception as exc:
            last_error = exc
            if verbose:
                print(f"[llm] error chunk={chunk_label} attempt={attempt} error={exc}")
            if attempt < max_retries:
                if stop_event is not None and stop_event.is_set():
                    raise RuntimeError("document processing cancelled")
                retry_after = getattr(exc, "response", None)
                if retry_after is not None:
                    retry_after = getattr(retry_after, "headers", {}).get("Retry-After")
                try:
                    sleep_seconds = (
                        float(retry_after)
                        if retry_after is not None
                        else retry_delay * attempt
                    )
                except (TypeError, ValueError):
                    sleep_seconds = retry_delay * attempt
                sleep_seconds = max(retry_delay, sleep_seconds)
                if stop_event is not None:
                    if stop_event.wait(sleep_seconds):
                        raise RuntimeError("document processing cancelled")
                else:
                    time.sleep(sleep_seconds)
    raise RuntimeError(
        f"OpenRouter request failed after {max_retries} attempts: {last_error}"
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def screen_one_doc(
    *,
    doc_dir: Path,
    input_folder: str,
    accepted_folder: str,
    rejected_folder: str,
    report_name: str,
    api_key: str,
    base_url: str,
    model: str,
    temperature: float,
    max_retries: int,
    retry_delay: float,
    max_concurrency: int,
    requests_per_minute: int,
    write_report_file: bool,
    verbose: bool = False,
    stop_event: threading.Event | None = None,
) -> dict[str, Any]:
    input_dir = doc_dir / input_folder
    accepted_dir = doc_dir / accepted_folder
    rejected_dir = doc_dir / rejected_folder
    if not input_dir.exists():
        raise FileNotFoundError(f"Input folder does not exist: {input_dir}")

    reset_output_dir(accepted_dir)
    reset_output_dir(rejected_dir)

    chunk_files = sorted(input_dir.glob("*.json"))
    print(
        f"[doc] start doc={doc_dir.name} input={input_dir.name} chunks={len(chunk_files)} "
        f"max_concurrency={max_concurrency} rpm={requests_per_minute}"
    )

    accepted = 0
    rejected = 0
    report_items: list[dict[str, Any]] = []
    report_lock = threading.Lock()
    rate_limiter = RequestRateLimiter(requests_per_minute)

    def process_one(index: int, json_file: Path) -> dict[str, Any]:
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("document processing cancelled")
        payload = load_payload(json_file)
        chunk = payload.get("chunk", payload)
        source_text = str(chunk.get("content", "") or "")
        user_prompt = build_user_prompt(payload)
        chunk_id = chunk.get("chunk_id", "")
        chunk_label = f"{doc_dir.name}:{chunk_id or json_file.stem}"
        if verbose:
            print(
                f"[doc] chunk {index}/{len(chunk_files)} "
                f"file={json_file.name} chunk_id={chunk_id} chars={len(source_text)}"
            )

        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("document processing cancelled")
        rate_limiter.wait_for_slot()
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("document processing cancelled")
        raw_response = ""
        try:
            raw_response = call_openrouter(
                api_key=api_key,
                base_url=base_url,
                model=model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=temperature,
                max_retries=max_retries,
                retry_delay=retry_delay,
                verbose=verbose,
                chunk_label=chunk_label,
                stop_event=stop_event,
            )
            parsed = extract_json_object(raw_response)
            normalized = normalize_record(parsed, source_text)
            normalized = postprocess_record(normalized, chunk.get("breadcrumb", ""))
        except Exception as exc:
            normalized = {
                "status": "reject",
                "equipment": "",
                "fault": "",
                "symptom": "",
                "causes": [],
                "actions": [],
                "consequences": [],
                "precautions": [],
                "key_components": [],
                "source_text": source_text,
                "rejection_reason": f"LLM调用或解析失败: {exc}",
                "extraction_notes": "调用失败或解析失败，已自动降级为 reject。",
            }
            if verbose:
                print(
                    f"[doc] extract_error chunk={chunk_id or json_file.stem} error={exc}"
                )

        if normalized["status"] == "ok":
            output_payload = {
                "doc_name": payload.get("doc_name", ""),
                "chapter": payload.get("chapter", ""),
                "source_chunk_file": json_file.name,
                "source_chunk_id": chunk_id,
                "record": normalized,
            }
            write_json(
                accepted_dir / f"record__{chunk_id or json_file.stem}.json",
                output_payload,
            )
            if verbose:
                print(f"[doc] accepted chunk={chunk_id or json_file.stem}")
        else:
            output_payload = {
                "doc_name": payload.get("doc_name", ""),
                "chapter": payload.get("chapter", ""),
                "source_chunk_file": json_file.name,
                "source_chunk_id": chunk_id,
                "raw_chunk": payload,
                "rejection": normalized,
            }
            write_json(
                rejected_dir / f"reject__{chunk_id or json_file.stem}.json",
                output_payload,
            )
            if verbose:
                print(f"[doc] rejected chunk={chunk_id or json_file.stem}")

        return {
            "file_name": json_file.name,
            "chunk_id": chunk_id,
            "breadcrumb": chunk.get("breadcrumb", ""),
            "status": normalized["status"],
            "equipment": normalized["equipment"],
            "fault": normalized["fault"],
            "rejection_reason": normalized["rejection_reason"],
            "raw_response": raw_response,
        }

    next_index = 0
    done_count = 0
    max_workers = max(1, max_concurrency)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}
        while next_index < len(chunk_files) and len(future_map) < max_workers:
            if stop_event is not None and stop_event.is_set():
                break
            json_file = chunk_files[next_index]
            next_index += 1
            future_map[executor.submit(process_one, next_index, json_file)] = (
                json_file.name
            )

        while future_map:
            if stop_event is not None and stop_event.is_set():
                for future in future_map:
                    future.cancel()
                raise RuntimeError("document processing cancelled")

            done_futures, _ = wait(
                future_map, timeout=0.5, return_when=FIRST_COMPLETED
            )
            if not done_futures:
                continue

            for future in done_futures:
                future_map.pop(future, None)
                done_count += 1
                result = future.result()
                with report_lock:
                    report_items.append(result)
                    if result["status"] == "ok":
                        accepted += 1
                    else:
                        rejected += 1
                print(
                    f"[doc] progress {done_count}/{len(chunk_files)} "
                    f"accepted={accepted} rejected={rejected} file={result['file_name']} status={result['status']}"
                )

            while next_index < len(chunk_files) and len(future_map) < max_workers:
                if stop_event is not None and stop_event.is_set():
                    break
                json_file = chunk_files[next_index]
                next_index += 1
                future_map[executor.submit(process_one, next_index, json_file)] = (
                    json_file.name
                )

    report_items.sort(key=lambda item: item["file_name"])
    report = {
        "doc_dir": str(doc_dir),
        "input_folder": input_folder,
        "accepted_folder": accepted_folder,
        "rejected_folder": rejected_folder,
        "model": model,
        "max_concurrency": max_concurrency,
        "requests_per_minute": requests_per_minute,
        "total_chunks": len(chunk_files),
        "accepted_chunks": accepted,
        "rejected_chunks": rejected,
        "items": report_items,
    }
    report_path = doc_dir / report_name
    if write_report_file:
        write_json(report_path, report)
        print(
            f"[doc] done doc={doc_dir.name} accepted={accepted} rejected={rejected} "
            f"report={report_path.name}"
        )
    else:
        print(f"[doc] done doc={doc_dir.name} accepted={accepted} rejected={rejected}")
    return report


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    api_key = ensure_api_key()
    chunks_root = resolve_chunks_root(args)
    doc_dirs = resolve_doc_dirs(args)
    print(
        f"[run] docs={len(doc_dirs)} input_folder={args.input_folder} "
        f"accepted_folder={args.accepted_folder} rejected_folder={args.rejected_folder} "
        f"model={args.model} max_concurrency={args.max_concurrency} rpm={args.requests_per_minute}"
    )

    summary: list[dict[str, Any]] = []
    for doc_dir in doc_dirs:
        print(f"\n===== LLM extract/reject: {doc_dir.name} =====")
        report = screen_one_doc(
            doc_dir=doc_dir,
            input_folder=args.input_folder,
            accepted_folder=args.accepted_folder,
            rejected_folder=args.rejected_folder,
            report_name=args.report_name,
            api_key=api_key,
            base_url=args.base_url,
            model=args.model,
            temperature=args.temperature,
            max_retries=args.max_retries,
            retry_delay=args.retry_delay,
            max_concurrency=args.max_concurrency,
            requests_per_minute=args.requests_per_minute,
            write_report_file=args.write_report,
            verbose=args.verbose,
        )
        summary.append(
            {
                "doc_dir": report["doc_dir"],
                "total_chunks": report["total_chunks"],
                "accepted_chunks": report["accepted_chunks"],
                "rejected_chunks": report["rejected_chunks"],
            }
        )
        print(
            f"[run] doc_result accepted={report['accepted_chunks']} rejected={report['rejected_chunks']} "
            f"-> {doc_dir / args.accepted_folder}"
        )

    if args.write_root_summary:
        summary_path = chunks_root / "llm_extract_summary.json"
        write_json(summary_path, {"documents": summary})
        print(f"\nsummary: {summary_path}")


if __name__ == "__main__":
    main()
