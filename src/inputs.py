# 输入文本格式模块
# src/inputs.py
"""把 A 的解析结果包装成模型输入。本文件只做"包装"，不做任何数值计算。"""

import re
from decimal import Decimal, InvalidOperation

MAX_ITEMS = 40            # 一次最多让模型处理的科目/条款条数

# ---------- 科目归一化：给科目名，问它对应哪个标准科目 ----------
def build_match_item(headers: list[str], standard_items: list[dict]) -> dict:
    assert headers, "headers 不能为空"
    assert len(headers) <= MAX_ITEMS, f"一次最多 {MAX_ITEMS} 条，请分批"
    return {
        "task": "match_item",
        "headers": headers[:MAX_ITEMS],
        "standard_items": standard_items,   # [{"code":"REVENUE","name":"营业收入"}, ...]
    }

# ---------- 研报引用匹配：判断"这句话在说哪个指标"，不判断对错 ----------
def build_match_claim(claims: list[dict], fields: list[dict]) -> dict:
    """claims: [{"claim_id":"c01","sentence":"...30字内..."}]
       fields: [{"item_code":"REVENUE","period":"2025"}]  ← 只给科目和期间，不给金额"""
    safe_claims = [{"claim_id": c["claim_id"], "sentence": c["sentence"][:500]}
                   for c in claims[:MAX_ITEMS]]
    return {"task": "match_claim", "claims": safe_claims, "fields": fields}

# ---------- 异常原因解释：把候选原因给它，让它做选择题 ----------
def build_explain_anomaly(rule_id: str, diff_desc: str, chunk_id: str,
                          quote: str, candidates: list[str]) -> dict:
    return {
        "task": "explain_anomaly",
        "rule_id": rule_id,
        "diff_desc": diff_desc,        # 程序生成的"差异有多大"的自然语言，不含明细数值
        "chunk_id": chunk_id,
        "quote": quote[:200],
        "candidates": candidates,      # ["UNIT_SCALE","PERIOD_MISMATCH","SCOPE_MIXED","..."]
    }

# ---------- 语义审计：给模型足够空间，自由指出程序规则覆盖不到的问题 ----------
MAX_AUDIT_CLAIMS = 20        # 单批最多送多少条 claim（与 match_claim 同量级，便于控成本）
MAX_AUDIT_FINDINGS = 40      # 最多带多少条程序结论供模型避重


def build_audit_claims(claims: list[dict], program_findings: list[dict],
                       fields: list[dict]) -> dict:
    """语义审计输入：研报原句 + 程序已给出的结论 + 可比对指标清单。

    三部分都不含"程序不知道的数值"：
      - claims 是研报原文（程序本就持有），带页码/段号，供模型给出可复核的位置；
      - program_findings 是程序自己的结论（含 status/cause/reason），只为让模型"别重复报
        已经判过的同一处问题"，不作为它判断的依据；
      - fields 只给科目与期间（与 match_claim 同一约束，见 interfaces.md §5.1）。
    模型可以自由描述问题类型与"应该是多少"，但这些文字**不参与任何计算**：
    程序只用它把疑似问题列进「待人工复核」，绝不据此改写报表数字。
    """
    safe_claims = [{
        "claim_id": c.get("claim_id"),
        "sentence": str(c.get("sentence") or "")[:500],
        "page": c.get("page"),
        "para_no": c.get("para_no"),
    } for c in claims[:MAX_AUDIT_CLAIMS]]
    safe_prog = [{
        "claim_id": f.get("claim_id"),
        "rule_id": f.get("rule_id"),
        "status": f.get("status"),
        "cause": f.get("cause") or "",
        "reason": str(f.get("reason") or "")[:120],
        "truth": str((f.get("right") or {}).get("value") or ""),
    } for f in program_findings[:MAX_AUDIT_FINDINGS]]
    return {"task": "audit_claims", "claims": safe_claims,
            "program_findings": safe_prog, "fields": fields[:MAX_ITEMS]}


# ---------- 研报式评价：程序把确定数字喂给模型，模型写分析话，数字白名单校验 ----------
MAX_COMMENT_METRICS = 24     # 最多喂多少条核心经营指标，控制篇幅与成本
MAX_COMMENT_ANOMALIES = 20
MAX_COMMENT_RESEARCH = 20

# 供评价聚焦的核心科目（与研报点评最相关的收入/利润/现金流/资产负债主项）
COMMENT_CORE = ("TOTAL_REVENUE", "REVENUE", "NP_PARENT", "NET_PROFIT",
                "OPERATING_COST", "CF_OPERATING", "TOTAL_ASSETS", "TOTAL_LIAB",
                "TOTAL_EQUITY", "REVENUE_MOUTAI", "REVENUE_SERIES",
                "REVENUE_IMOUTAI")


def _normalize_number(s: str) -> set[str]:
    """把一个数字串展开成多个**数值等价**的字符串写法，避免被同等数值不同写法误拦截。

    例："174140000000.00" → {"174140000000.00", "174140000000"}，
        "87.50"          → {"87.50", "87.5"}。
    括大的是**写法变体**，不是新数值——模型仍无法编造任何它没见过的数量。
    """
    clean = s.replace(",", "")
    out = {clean}
    try:
        d = Decimal(clean)
        if d == d.to_integral_value():
            out.add(str(d.to_integral_value()))
        else:
            out.add(format(d.normalize(), "f"))
    except InvalidOperation:
        pass
    return out


def collect_allowed_numbers(obj) -> set[str]:
    """从要喂给模型的 payload 里抽**程序已确认的全部数字**，去逗号后作为白名单。

    模型评价文本中出现的每个数字都必须落在这个集合里——它只能引用程序给它的数，
    不能自己编一个（如把它没看到的"毛利率=92%"算出来）。
    """
    nums = set()
    if isinstance(obj, dict):
        for v in obj.values():
            nums |= collect_allowed_numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            nums |= collect_allowed_numbers(v)
    elif isinstance(obj, (int, float)):
        s = f"{obj:g}" if isinstance(obj, float) else str(obj)
        nums |= _normalize_number(s)
    elif isinstance(obj, str):
        # ★ 必须带符号匹配：程序派生值里有负数（如同比 -1.20），若丢掉负号，
        #   白名单里只剩 "1.20"，模型照抄 "-1.20" 反而会被误判为编造数字。
        for m in re.findall(r"[+-]?\d[\d,]*\.?\d*", obj):
            nums |= _normalize_number(m)
    return nums


MAX_COMMENT_DERIVED = 40
MAX_COMMENT_ANALYSIS = 40


def build_gen_commentary(metrics: list[dict], csi: dict, anomalies: list[dict],
                         research_fail: list[dict], grade_label: str,
                         derived: list[dict] | None = None,
                         analysis: dict | None = None) -> dict:
    """研报式评价输入：把所有"程序已算好的确定数字"喂给模型，让它写一段分析评价。

    metrics: [{"code","name","period","value"}] —— 程序提取的经营指标值（带确定数值）
    csi:     {"csi": 87.5, "grade": "A", "grade_label": "基本一致"}
    anomalies:   异常清单（含确定 diff / tolerance）
    research_fail: 研报引用比对不一数（含研报值/财报值/差异）
    derived: src.derived.compute_derived 的产物 —— 同比/比率/亿元换算/情景测算外推
    analysis: src.financial_analysis.analyze 的产物 —— 盈利能力/费用率/趋势/结构四组
              （与 derived 同 code 的条目在此按 code 去重，derived 优先，避免重复喂入）

    ★ 与其他任务相反：这里**给数字**，因为用户要的是"根据数据做评价"。
    但数字一律来自程序产物（提取值 + 派生计算值 + 财务分析值），模型只负责组织语言、
    不做任何运算；输出再经 collect_allowed_numbers 白名单兜底，任何程序没给的数字
    都会被拦截（src/tasks._post_checks）。
    """
    core = [{
        "code": m.get("code", ""),
        "name": m.get("name", m.get("code", "")),
        "period": str(m.get("period", "")),
        "value": str(m.get("value", "")),
    } for m in metrics if m.get("code") in COMMENT_CORE][:MAX_COMMENT_METRICS]

    safe_anom = [{
        "dimension": a.get("dimension", ""),
        "item_code": a.get("item_code", ""),
        "status": a.get("status", ""),
        "diff": str(a.get("diff", "")),
        "tolerance": str(a.get("tolerance", "")),
    } for a in anomalies[:MAX_COMMENT_ANOMALIES]]

    safe_research = [{
        "item_code": r.get("item_code", ""),
        "period": str(r.get("period", "")),
        "report_value": str((r.get("left") or {}).get("value", "")),
        "truth_value": str((r.get("right") or {}).get("value", "")),
        "diff": str(r.get("diff", "")),
        "cause": r.get("cause", ""),
    } for r in research_fail[:MAX_COMMENT_RESEARCH]]

    safe_derived = [{
        "code": d.get("code", ""),
        "name": d.get("name", ""),
        "period": str(d.get("period", "")),
        "value": str(d.get("value", "")),
        "unit": d.get("unit", ""),
        "basis": d.get("basis", ""),
    } for d in (derived or [])[:MAX_COMMENT_DERIVED]]

    # 财务分析结果并入：按 code 对 derived 去重（derived 优先），避免同一指标喂两遍
    derived_codes = {d["code"] for d in safe_derived}
    src_analysis = analysis.get("metrics") if isinstance(analysis, dict) else analysis
    safe_analysis = [{
        "group": m.get("group", ""),
        "code": m.get("code", ""),
        "name": m.get("name", ""),
        "period": str(m.get("period", "")),
        "value": str(m.get("value", "")),
        "unit": m.get("unit", ""),
        "basis": m.get("basis", ""),
    } for m in (src_analysis or []) if m.get("code") not in derived_codes][:MAX_COMMENT_ANALYSIS]

    return {
        "task": "gen_commentary",
        "metrics": core,
        "derived": safe_derived,
        "analysis": safe_analysis,
        "csi": {"csi": csi.get("csi"),
                "grade": csi.get("grade", ""),
                "grade_label": grade_label},
        "anomalies": safe_anom,
        "research": safe_research,
        # ★ 显式给出各清单条数：模型可引用"有 N 条…存在差异"这类**程序已知的计数**，
        #   数字白名单据此放行（不得自行推算任何新数）。
        "counts": {
            "metrics": len(core),
            "derived": len(safe_derived),
            "analysis": len(safe_analysis),
            "anomalies": len(safe_anom),
            "research": len(safe_research),
        },
    }
