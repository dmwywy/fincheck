# src/forecast.py
"""盈利预测基础模块（对应选题4：分产品收入预测 + PE 估值情景测算）。

设计铁律（与 src/financial_analysis.py、src/derived.py 一致）：
  1. 只做加/减/乘/除，**不引入股价、市值、股本、一致预期**等外部数据；
  2. 每条结果都带 basis（口径说明），可溯源、可复核；
  3. name / unit / basis 文本一律不含阿拉伯数字——避免公式常数混进
     `src.inputs.collect_allowed_numbers` 的数字白名单；
  4. 输入缺失**不抛异常**：对应指标缺席并在 warnings 写明原因。

本模块只做"情景测算"，明确非盈利预测、非投资建议，不给出投资评级或目标价。
"""
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

YI = Decimal("100000000")                 # 元 → 亿元
GROUP_FORECAST = "forecast"
GROUP_ZH = {GROUP_FORECAST: "盈利预测（情景测算）"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")   # 时点：YYYY-MM-DD

# 分产品收入预测：以最近一期该产品同比增速外推；若该产品缺比较期，退化为整体收入同比
PRODUCTS = (("REVENUE_MOUTAI", "茅台酒"), ("REVENUE_SERIES", "系列酒"))
# PE 情景倍数（**假设值**，仅作情景测算，不代表市场或分析师预期）
PE_MULTIPLES = (15, 20, 25, 30)
_CN_NUM = {15: "十五", 20: "二十", 25: "二十五", 30: "三十"}


def _dec(v) -> Decimal | None:
    try:
        return Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError):
        return None


def _year(p) -> int | None:
    m = re.match(r"^(\d{4})", str(p or ""))
    return int(m.group(1)) if m else None


def _num(d: Decimal, nd: int) -> str:
    q = d.quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP)
    if q == 0:
        q = abs(q)                          # 消除 -0.0 这种写法
    return format(q, "f")


def _metric(code: str, name: str, period, d: Decimal, unit: str, basis: str) -> dict:
    return {"group": GROUP_FORECAST, "code": code, "name": name, "period": str(period or ""),
            "value": _num(d, 1), "unit": unit, "basis": basis}


def _index(records) -> dict:
    """→ {(科目, 年份): 金额}；只收时期数（时点数与预测无关，直接忽略）。"""
    fy: dict = {}
    if not isinstance(records, list):
        return fy
    for r in records:
        if not isinstance(r, dict):
            continue
        code = r.get("item_code")
        period = str(r.get("period") or "")
        d = _dec(r.get("value"))
        y = _year(period)
        if not code or d is None or y is None or _DATE_RE.match(period):
            continue
        fy[(code, y)] = d
    return fy


def _project_products(fy: dict, warnings: list[str]) -> list[dict]:
    """分产品收入预测：下一期 = 本期 × (一 + 该产品同比增速)。"""
    years = sorted({y for _c, y in fy})
    if len(years) < 2:
        warnings.append("只有一期收入数据，未做分产品收入预测")
        return []
    y, p = years[-1], years[-2]
    rev, prev_rev = fy.get(("REVENUE", y)), fy.get(("REVENUE", p))
    base_g = ((rev - prev_rev) / prev_rev if rev is not None and prev_rev not in (None, 0)
              else None)
    out: list[dict] = []
    for code, name in PRODUCTS:
        cur = fy.get((code, y))
        if cur is None:
            continue
        pre = fy.get((code, p))
        g = ((cur - pre) / pre if pre not in (None, 0) else base_g)
        if g is None:
            warnings.append(f"缺少{name}可比基数，未做{name}收入预测")
            continue
        out.append(_metric(f"PROD_FORE_{code}", f"{name}收入预测值", f"{y + 1}E",
                           cur * (Decimal(1) + g) / YI, "亿元",
                           "口径：按最近一期同比增速外推的下一期收入测算值，非盈利预测。"))
    return out


def _pe_scenarios(fy: dict, warnings: list[str]) -> list[dict]:
    """PE 估值情境：隐含市值 = 归母净利润 × 假设市盈率。"""
    years = sorted({y for _c, y in fy})
    if not years:
        warnings.append("没有利润表期间数据，未做市盈率情景测算")
        return []
    y = years[-1]
    npp = fy.get(("NP_PARENT", y))
    if npp is None:
        warnings.append("缺少归母净利润，未做市盈率情景测算")
        return []
    npp_yi = npp / YI
    out: list[dict] = []
    for m in PE_MULTIPLES:
        out.append(_metric(f"PE_MCAP_{m}",
                           f"隐含市值（按{_CN_NUM.get(m, m)}倍市盈率情景）", f"{y}E",
                           npp_yi * m, "亿元",
                           "口径：归属母公司股东净利润乘以假设市盈率的情景测算，"
                           "非盈利预测、非投资建议。"))
    return out


def forecast(records) -> dict:
    """入口：分产品收入预测 + PE 情景测算。

    → {"metrics": [{group, code, name, period, value, unit, basis}, ...],
       "warnings": ["…缺失…，未计算…", ...]}
    输入非法或为空时不抛异常，返回空 metrics 并在 warnings 说明原因。
    """
    fy = _index(records)
    if not fy:
        return {"metrics": [], "warnings": ["记录里没有可解析的期间数值，未做盈利预测"]}
    warnings: list[str] = []
    metrics: list[dict] = []
    try:
        metrics += _project_products(fy, warnings)
        metrics += _pe_scenarios(fy, warnings)
    except (ArithmeticError, TypeError, ValueError) as exc:   # noqa: BLE001
        return {"metrics": metrics,
                "warnings": warnings + [f"预测过程出现异常，已跳过：{type(exc).__name__}"]}
    return {"metrics": metrics, "warnings": warnings}
