# src/tasks.py
"""任务级接口：加载提示词 -> 调用闸门 -> schema 校验 -> 失败修复重试。
上层（pipeline / cli）只使用本模块，不直接接触 src.llm.call。"""
from pathlib import Path

import yaml

from src.llm import call
from src.schemas import SCHEMA_BY_TASK
from src.manifest import sha256_text
from src.validators import (check_commentary_sections, check_enum_whitelist,
                            check_evidence, check_no_amounts)

ROOT = Path(__file__).resolve().parents[1]
PROMPT_DIR = ROOT / "prompts"
FIELDS_YAML = ROOT / "rules" / "fields.yaml"
DEFAULT_VERSION = "v1"

# 异常原因候选（冻结）：5 类确定性归因 + 1 个"无法判断"兜底
ALLOWED_CAUSES = {"UNIT_SCALE", "PERIOD_MISMATCH", "SCOPE_MIXED",
                  "VALUE_ERROR", "SIGN_ERROR", "UNCERTAIN"}

# 证据由程序回填的任务：模型只做语义判断，不产出位置（interfaces.md §3.1a、§3.3a）
PROGRAM_EVIDENCE_TASKS = {"match_item", "match_claim"}


def _check_audit_findings(data: dict, context: dict) -> list[str]:
    """语义审计输出校验：claim 必须来自本次输入，evidence_quote 必须**逐字**在原句中。

    ★ 不做"数值正确性"校验——审计的价值恰恰在于发现规则覆盖不到的问题，用程序去验
      它的结论等于把它降级成又一条规则。但"引用的原句是否真实存在"是可验证的客观事实，
      必须验：编造证据的结论一律拦下，否则报告里会出现无法复核的伪条目。
    """
    errs: list[str] = []
    sents = context.get("audit_claims") or {}
    allowed = context.get("causes") or set()
    for f in data.get("findings") or []:
        cid = f.get("claim_id")
        if cid not in sents:
            errs.append(f"claim_id 不在本次输入中（疑似编造）：{cid}")
            continue
        if f.get("cause") and allowed and f["cause"] not in allowed:
            errs.append(f"cause 越界：{f['cause']}")
        quote = (f.get("evidence_quote") or "").strip()
        if not quote:
            errs.append(f"{cid} 的 evidence_quote 为空：必须逐字引用研报原句作为依据")
        elif quote not in sents[cid]:
            errs.append(f"{cid} 的 evidence_quote 不是原句片段（疑似编造）：{quote[:40]}")
    return errs


def load_item_codes() -> set[str]:
    """从 rules/fields.yaml 读出标准科目编号，作为模型 item_code / canonical_item 的合法取值。"""
    with FIELDS_YAML.open(encoding="utf-8") as f:
        fields = yaml.safe_load(f)["fields"]
    return set(fields)


def load_prompt(task: str, version: str = DEFAULT_VERSION) -> tuple[str, dict]:
    """读取提示词原文，并返回其内容哈希（供 manifest 记录）。
    兼容两种命名：<task>.txt 与 <task>（无扩展名）。"""
    p = PROMPT_DIR / f"{task}.txt"
    if not p.exists():
        alt = PROMPT_DIR / task
        if alt.exists():
            p = alt
        else:
            raise FileNotFoundError(
                f"提示词文件不存在：{p}（也未找到无扩展名的 {alt}）。"
                f"约定文件名为 prompts/<task>.txt，可用任务：{list(SCHEMA_BY_TASK)}"
            )
    text = p.read_text(encoding="utf-8")
    meta = {"prompt_id": task, "version": version, "sha256": sha256_text(text),
            "file": p.name}
    return text, meta


def _post_checks(data: dict, task: str, context: dict | None) -> list[str]:
    """第二/三层校验：枚举白名单 + 证据真实性 + 报告数字白名单。
    context 可携带 {"chunks": {chunk_id: text}, "allowed_numbers": set[str]}。
    返回错误列表，空列表表示通过。"""
    errs = []
    errs += check_enum_whitelist(data, {"item_codes": load_item_codes(),
                                        "causes": ALLOWED_CAUSES})
    ctx = context or {}
    chunks = ctx.get("chunks") or {}
    # 证据由程序回填的任务，模型不产出位置，故不校验模型证据
    if task not in PROGRAM_EVIDENCE_TASKS and data.get("evidence") and chunks:
        ok, msg = check_evidence(data["evidence"], chunks)
        if not ok:
            errs.append(msg)
    if task == "gen_commentary":
        if ctx.get("allowed_numbers"):
            errs += check_no_amounts(data.get("text", ""), ctx["allowed_numbers"])
        # 小节名白名单：拦越界小节名 + 必含【核心观点】；其余小节不强制齐备
        errs += check_commentary_sections(data.get("text", ""))
    if task == "audit_claims":
        errs += _check_audit_findings(data, {**ctx, "causes": ALLOWED_CAUSES})
    return errs


def call_task(task: str, payload: dict, run_id: str, user_id: str = "team",
              offline: bool = False, context: dict | None = None,
              api_key: str | None = None) -> tuple[dict | None, str]:
    """返回 (通过全部校验的结果 dict 或 None, 状态字符串)。
    校验顺序：Pydantic 结构 → 枚举白名单 → 证据真实性 → 数字白名单；任一失败修复重试一次。"""
    if task not in SCHEMA_BY_TASK:
        raise KeyError(f"未注册的任务：{task}（可用：{list(SCHEMA_BY_TASK)}）")
    prompt_text, _meta = load_prompt(task)
    schema_cls = SCHEMA_BY_TASK[task]

    data, status = call(prompt_text, payload, run_id=run_id,
                        user_id=user_id, task=task, offline=offline, api_key=api_key)
    if data is None:
        return None, status

    for attempt in range(2):
        try:
            validated = schema_cls.model_validate(data).model_dump()
            errs = _post_checks(validated, task, context)
        except Exception as e:
            errs = [str(e)]
        if not errs:
            return validated, status + ("|SCHEMA_FIXED" if attempt else "")
        if attempt == 0:
            # 修复重试一次：把校验错误回传，要求只按样例输出
            fix_payload = dict(payload)
            fix_payload["_fix"] = (f"上次输出未通过校验：{'；'.join(errs)}。"
                                   "请严格按 json 样例输出，只包含约定字段与合法取值。")
            data, status = call(prompt_text, fix_payload, run_id=run_id,
                                user_id=user_id, task=task, offline=offline,
                                api_key=api_key)
            if data is None:
                return None, status + "|SCHEMA_FIX_NO_RESULT"
    return None, "SCHEMA_INVALID:" + ";".join(errs[:3])