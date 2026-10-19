"""Web upload orchestration shared by the Streamlit form and local acceptance."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED = {".pdf", ".docx", ".docm"}
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def run_uploaded_reports(
    uploaded_files: Iterable[object],
    *,
    run_id: str,
    default_year: int,
    offline: bool,
    report_period: str = "annual",
    api_key: str | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """Persist upload payloads, run ingestion/checks, then build all reports.

    ``uploaded_files`` accepts Streamlit UploadedFile instances. The same public
    entry point also makes the complete upload-processing path callable in a
    local acceptance harness without browser-specific file chooser access.
    """
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("运行编号仅允许 1–80 位英文字母、数字、下划线或连字符")
    # ★ 期间必须显式校验：半年的流量科目按年初至 6-30 累计（YTD）、期末余额取 6-30。
    #   漏传或传错会让半年报被按年报列头抽取、期间标成全年 FY，结论全错。
    if report_period not in ("annual", "half_year"):
        raise ValueError(f"不支持的报告期间：{report_period}（可选 annual / half_year）")
    files = list(uploaded_files)
    if not files:
        raise ValueError("请先选择至少一份年报文件。")

    prepared: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for uploaded in files:
        safe_name = Path(str(uploaded.name).replace("\\", "/")).name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in SUPPORTED:
            raise ValueError(f"不支持的文件类型：{safe_name}")
        if not safe_name or safe_name in {".", ".."}:
            raise ValueError("上传文件名无效")
        if safe_name.casefold() in seen:
            raise ValueError(f"上传文件名重复：{safe_name}")
        seen.add(safe_name.casefold())
        payload = uploaded.getvalue()
        if not payload:
            raise ValueError(f"上传文件为空：{safe_name}")
        prepared.append((safe_name, payload))

    run_root = ROOT / "runs" / run_id
    input_dir = run_root / "uploaded"
    input_dir.mkdir(parents=True, exist_ok=False)
    for safe_name, payload in prepared:
        (input_dir / safe_name).write_bytes(payload)

    from src.ingest import process_folder
    from src.pipeline import run as run_pipeline
    from src.report import build_all

    progress = on_progress or (lambda _message: None)
    progress("识别报告并抽取财务字段")
    ingest_result = process_folder(
        input_dir, run_root / "ingested", default_year=int(default_year),
        report_period=report_period,
    )
    # ★ 分类日志必须落盘：哪份材料被当成年报、哪份被当成研报、哪份**没被识别**，
    #   是使用者判断"这份文件到底有没有被核查"的唯一依据。只 print 到 stdout
    #   在网页端完全看不到，未识别的文件就会被当成已核查，属于误导。
    #   放运行根目录而非 ingested/：load_structured 与 report.collect 都用
    #   非递归 glob("*.json") 读 ingested/，日志放进去会被当成字段记录。
    (run_root / "ingest_log.json").write_text(
        json.dumps(ingest_result.get("log") or [], ensure_ascii=False,
                   indent=2, sort_keys=True),
        encoding="utf-8")

    # ★ 一份年报都没识别出来时**不报错**：照常用（空的）本轮 ingested 目录跑完流程，
    #   产出一份"未识别到可核查字段"的说明性报告。上传任意 PDF/DOCX 都应拿回报告，
    #   而不是一句异常——异常什么结果都不给，用户无从判断到底发生了什么。
    progress("运行模型任务与确定性规则核查")
    claims_path = ingest_result["claims_path"]
    run_pipeline(
        "configs/demo.yaml", offline=offline, run_id=run_id,
        claims_override=(str(claims_path.relative_to(ROOT)) if claims_path else None),
        data_dir_override=str((run_root / "ingested").relative_to(ROOT)),
        skip_claims=claims_path is None,
        # ★ 使用者可在网页表单临时填入 Key；不填则回落到 .env。
        #   它只用于构造出站客户端，不进日志/报告/缓存键。
        api_key=(api_key or None),
    )

    progress("生成 PDF、Excel 和网页报告")
    made = build_all(run_id, ("pdf", "xlsx", "html"))
    return {"run_root": run_root, "ingest": ingest_result, "reports": made}
