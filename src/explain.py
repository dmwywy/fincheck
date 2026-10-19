# src/explain.py
"""异常原因解释：把程序查出的不一致交给模型做"选择题"，再由程序校验。

职责边界（对应 interfaces.md §5.1 / §5.3）：
  - 程序：算出差异、给出候选原因、**不把具体金额交给模型**；
  - 模型：从候选中选一个，写一句不超过 60 字的解释，**不产出任何数值**；
  - 程序：校验 cause 必须在候选内、解释里不得含金额，否则整条丢弃。

为什么不让模型自由发挥：差异归因必须可复核。候选人由程序按确定性规则给出
（是否为 10 的整数次幂、是否等于上期数、是否符号相反），模型只负责措辞。
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from src.inputs import build_explain_anomaly
from src.pipeline import ROOT, _write_json
from src.tasks import ALLOWED_CAUSES, call_task

MAX_EXPLAIN = 20          # 单次运行最多解释多少条，控制调用成本

# 解释文字里出现数字即判不合格——prompt 已禁止，此处做程序侧兜底
NUMBER_RE = re.compile(r"\d")


def _diff_desc(r: dict) -> str:
    """"差异有多大"的定性描述：只给量级关系，不给具体金额，也不暗示结论。

    ★ 必须中性。此前对"数值接近但单位写法非法"的情形写了"差额略超容差，
      接近取整范围"，把模型的注意力引到数值上，实测它把畸形单位误判为
      VALUE_ERROR。差异若不在数值上，就要如实说"差异不在数值上"。
    """
    reason = r.get("reason") or ""
    # 单位写法类问题（cause=UNIT_SCALE；文案曾为"单位写法非法"，现为"单位写法有误"）
    # 差异不在数值上——必须如实这么描述，否则会把模型的注意力引到数值上。
    if r.get("cause") == "UNIT_SCALE" or "单位写法" in reason:
        return "两者的数值本身接近，差异不在数值大小上，而在其余表述上"
    try:
        d, t = abs(Decimal(str(r.get("diff")))), Decimal(str(r.get("tolerance") or "0"))
    except (InvalidOperation, TypeError):
        return "程序判定该差异超出容差"
    if t <= 0:
        return "容差为零，任何差异即判不符"
    ratio = d / t
    if ratio < 2:
        return "差额略超容差，量级与容差同阶"
    if ratio < 10:
        return "差额明显超过容差"
    if ratio < 1000:
        return "差额远超容差"
    return "差额与容差相差数个量级"


def load_explanations(run_id: str) -> dict[str, dict]:
    """读回 explanations.json，按编号索引，供报告/网页与结果文件关联展示。

    结果文件里**不含**模型解释（那是可复现性要求）；解释单独存放，
    展示时用 rule_id / claim_id 关联。
    """
    p = ROOT / "runs" / run_id / "rules" / "explanations.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for it in data.get("items", []):
        key = it.get("claim_id") or it.get("rule_id")
        if key:
            out[key] = it.get("explanation") or {}
    return out


def _rule_locator(r: dict) -> tuple[str, str]:
    """规则结果：证据是 chunk_id + raw_text。"""
    ev = r.get("evidence") or {}
    return ev.get("chunk_id", ""), ev.get("raw_text", "") or ""


def _research_locator(r: dict) -> tuple[str, str]:
    """研报结果：证据是研报原句，用 claim_id 作为可引用的片段标识。"""
    ev = r.get("evidence") or {}
    return r.get("claim_id", ""), ev.get("sentence", "") or ""


def explain_anomalies(rule_results: list[dict], run_id: str,
                      offline: bool = False,
                      research_results: list[dict] | None = None,
                      api_key: str | None = None) -> list[dict]:
    """为 FAIL / REVIEW 条目补充模型解释，返回补充后的结果（原地也更新）。

    只处理带证据的条目——没有证据就没法让模型解释，这类直接跳过，
    保持"无证据不进入核查"的一致口径。
    research_results 传研报维度的结果；不传则只解释规则维度。
    """
    todo = [(r, _rule_locator) for r in rule_results
            if r.get("status") in ("FAIL", "REVIEW")]
    todo += [(r, _research_locator) for r in (research_results or [])
             if r.get("status") in ("FAIL", "REVIEW")]
    todo = [(r, f) for r, f in todo if f(r)[0]][:MAX_EXPLAIN]

    statuses = []
    for r, locator in todo:
        cid, quote = locator(r)
        payload = build_explain_anomaly(
            rule_id=r.get("rule_id", ""),
            diff_desc=_diff_desc(r),
            chunk_id=cid,
            quote=quote,
            candidates=sorted(ALLOWED_CAUSES),
        )
        out, status = call_task(
            "explain_anomaly", payload, run_id, offline=offline,
            context={"chunks": {cid: quote}}, api_key=api_key)
        statuses.append(status)

        # 程序侧已有的确定性归因（研报维度会带 cause）；用于与模型判断对比
        program_cause = r.get("cause") or ""

        if not out:
            r["explanation"] = {"cause": "", "text": "", "confidence": 0.0,
                                "program_cause": program_cause,
                                "status": status, "note": "模型未给出解释，转人工复核"}
            continue
        # 程序侧兜底：解释文字不得含数字（模型可能不守规矩）
        text = out.get("explanation", "")
        if NUMBER_RE.search(text):
            r["explanation"] = {"cause": out.get("cause", ""), "text": "",
                                "confidence": 0.0, "program_cause": program_cause,
                                "note": "模型解释含数字，按规则丢弃"}
            continue
        r["explanation"] = {
            "cause": out.get("cause", ""),
            "text": text,
            "confidence": out.get("confidence", 0.0),
            "program_cause": program_cause,
            # 与程序确定性归因不一致时显式标出，不静默采信任一方
            "agrees_with_program": (not program_cause
                                    or out.get("cause") == program_cause),
            "status": status,
        }

    if todo:
        _write_json(run_id, "rules/explanations.json", {
            "run_id": run_id,
            "explained": len(todo),
            "status": "|".join(dict.fromkeys(statuses)),
            "items": [{"rule_id": r.get("rule_id"),
                       "claim_id": r.get("claim_id"),
                       "status": r.get("status"),
                       "explanation": r.get("explanation")} for r, _ in todo],
        })
    return rule_results
