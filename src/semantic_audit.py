# src/semantic_audit.py
"""C研 的**语义审计层**（RC06）：把确定性规则覆盖不到的语义型问题交给大模型指出。

为什么必须有这一层：确定性规则只能判"数值比得上/比不上"，对下面这类问题天然失明——
  - 同比**方向**写反（数字对得上幅度、符号相反，而规则只在有上下两期年度数时才能判）；
  - 期间/年份**语义**错配（把 2026年6月末的数写成某年度数；预测区间指向已结束年度）；
  - 单季/半年度与全年累计混用（研报写 Q4 数，程序只提了全年累计）；
  - 研报**内部**预测表的勾稽（调整幅度 vs(新值÷旧值−1)），财报里根本没有对应基准字段。
这些错误靠"再多加几条正则"是加不完的——清单外还有清单。所以把这类判断交给模型：
**不限错误类型**地审一遍，由它指出问题，程序只负责校验"它引用的原句真实存在"。

与其它模型任务的关键差别（也是本层的安全边界）：
  ★ 模型的结论**一律落「待人工复核」**，绝不升级为「异常」，更不改写任何数值——
    程序算出来的异常是可复现的，模型判断不可复现，两者不能混在一个计数口径里。
  ★ 程序已判定的 (claim_id, cause) 一律保留，模型结论只做**补充**：命中同一处的不重复报。
  ★ 离线/缓存未命中时优雅降级（返回 0 条），纯规则照常出报告，不因模型不可用而崩。
"""
from __future__ import annotations

import json
from pathlib import Path

from src.inputs import MAX_AUDIT_CLAIMS, build_audit_claims
from src.tasks import ALLOWED_CAUSES, call_task

ROOT = Path(__file__).resolve().parents[1]

RULE_ID = "RC06@v1.0"
MAX_AUDIT_TOTAL = 60          # 一轮最多审计多少条 claim：优先"程序判不了"的，其次"程序判通过"的
AUDIT_BATCH = MAX_AUDIT_CLAIMS
DEFAULT_CAUSE = "UNCERTAIN"   # 程序"判不了"的条目在去重键上等价于这个兜底原因


def _audit_order(rc: dict, claims: list[dict]) -> list[dict]:
    """待审 claim 的优先级：先 REVIEW（程序判不了、最可能藏着真问题）后 PASS，最后 FAIL。

    被程序判为异常的（FAIL）排在最后：它们已经在报告的异常清单里，审计对它们的增量最小，
    但并非没有——同一句话可能同时有第二类错误（如既单位错又方向错），故仍然送审。
    """
    status: dict[str, int] = {}
    for r in rc.get("results") or []:
        status[r.get("claim_id")] = 0 if r.get("status") == "FAIL" else 1
    for r in rc.get("review") or []:
        status.setdefault(r.get("claim_id"), 2)
    ranked = sorted(claims, key=lambda c: (-status.get(c.get("claim_id"), 1),
                                           str(c.get("claim_id"))))
    return ranked[:MAX_AUDIT_TOTAL]


def _program_keys(rc: dict) -> set[tuple]:
    """程序已判定的去重键 (claim_id, cause)。REVIEW 条目无 cause，按兜底原因计。"""
    keys = {(r.get("claim_id"), r.get("cause") or DEFAULT_CAUSE)
            for r in rc.get("results") or []}
    keys |= {(r.get("claim_id"), DEFAULT_CAUSE) for r in rc.get("review") or []}
    return keys


def _program_findings(rc: dict, ids: set[str]) -> list[dict]:
    """本批 claim 对应的程序结论，喂给模型只为让它"别重复报已经判过的问题"。"""
    out = []
    for r in list(rc.get("results") or []) + list(rc.get("review") or []):
        if r.get("claim_id") in ids:
            out.append(r)
    return out


def _finding_record(f: dict, claim: dict, rule_id: str) -> dict:
    """模型结论 → 待复核条目：形态与 RC01/RC02 的 review 条目一致，另带来源标识。

    ★ source="llm_audit" 必须显式带上：报告要能把"程序判不了"与"模型提示的疑似问题"
      分开呈现，读者才知道哪一条是模型说的、需要人工确认。
    """
    return {
        "rule_id": rule_id, "dimension": "C研", "claim_id": f.get("claim_id"),
        "item_code": None, "period": None, "status": "REVIEW",
        "source": "llm_audit",
        "cause": f.get("cause") or DEFAULT_CAUSE,
        "problem_type": f.get("problem_type") or "",
        "severity": (f.get("severity") or "MED").upper(),
        "expected_value": f.get("expected_value") or "",
        "reason": (f"模型语义审计提示：{f.get('problem_type') or '疑似不一致'}；"
                   f"{f.get('reason') or ''}").strip(),
        "evidence": {
            "file": claim.get("source"), "section": claim.get("section"),
            "sentence": claim.get("sentence"), "page": claim.get("page"),
            "para_no": claim.get("para_no"), "row_no": claim.get("row_no"),
            "caption": claim.get("caption"),
            "quote": f.get("evidence_quote") or "", "verified": True,
        },
    }


def run_semantic_audit(claims_path: str, rc: dict, run_id: str, offline: bool,
                       fields: list[dict] | None = None,
                       api_key: str | None = None) -> tuple[list[dict], str]:
    """对本次研报 claim 做一遍语义审计，返回 (待复核条目, 状态串)。

    分批调用，任一批失败/离线未命中都只影响该批（其余照常），绝不打断主流程。
    输出落盘 runs/<run_id>/llm/audit_findings.json，供报告与网页展示。
    """
    p = ROOT / claims_path
    if not p.exists():
        return [], "NO_CLAIMS"
    all_claims = json.loads(p.read_text(encoding="utf-8"))
    by_id = {c["claim_id"]: c for c in all_claims}
    targets = _audit_order(rc, all_claims)
    if not targets:
        return [], "NO_CLAIMS"

    prog_keys = _program_keys(rc)
    findings: list[dict] = []
    seen: set[tuple] = set()
    statuses: list[str] = []

    for i in range(0, len(targets), AUDIT_BATCH):
        batch = targets[i:i + AUDIT_BATCH]
        ids = {c["claim_id"] for c in batch}
        payload = build_audit_claims(batch, _program_findings(rc, ids), fields or [])
        ctx = {"audit_claims": {c["claim_id"]: str(c.get("sentence") or "")
                                for c in batch}}
        out, status = call_task("audit_claims", payload, run_id, offline=offline,
                                context=ctx, api_key=api_key)
        statuses.append(status)
        if not out:
            continue
        for f in out.get("findings") or []:
            cid = f.get("claim_id")
            key = (cid, f.get("cause") or DEFAULT_CAUSE)
            # ① 程序已判过同一处（同 claim 同原因）→ 不重复报；② 本批内去重。
            #    ★ 不同原因一律保留：一句话可以同时有单位错与方向错，两类都要能看见。
            if key in prog_keys or key in seen or cid not in by_id:
                continue
            seen.add(key)
            findings.append(_finding_record(f, by_id[cid], RULE_ID))

    out_path = ROOT / "runs" / run_id / "llm" / "audit_findings.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        {"run_id": run_id, "status": "|".join(dict.fromkeys(statuses)),
         "audited_claims": len(targets), "findings": findings},
        ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    if not any(s.startswith(("API_OK", "CACHE_HIT")) for s in statuses):
        return findings, "|".join(dict.fromkeys(statuses)) + "|NO_RESULT"
    return findings, f"FINDINGS={len(findings)}|" + "|".join(dict.fromkeys(statuses))


def load_findings(run_id: str) -> list[dict]:
    """读某次运行的语义审计结论；没有该文件返回空列表（旧产物兼容）。"""
    p = ROOT / "runs" / run_id / "llm" / "audit_findings.json"
    try:
        return (json.loads(p.read_text(encoding="utf-8")) or {}).get("findings") or []
    except (OSError, json.JSONDecodeError):
        return []


__all__ = ["RULE_ID", "run_semantic_audit", "load_findings", "ALLOWED_CAUSES"]
