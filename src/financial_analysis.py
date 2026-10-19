# src/financial_analysis.py
"""确定性财务分析模块（程序算数、模型不参与、结果可复现）。

按选题要求，把 B 阶段核定的结构化记录算成四组指标：
  ① 盈利能力：毛利率 / 净利率 / ROE / ROIC
  ② 费用率：  税金及附加率 / 销售费用率 / 管理费用率
  ③ 趋势：    核心科目同比增速 / 比率同比变动（pcts）
  ④ 结构：    分产品占比 / 分渠道占比 / 吨价

设计铁律（与 src/derived.py 一致）：
  1. 只做加/减/乘/除，不引入任何外部数据（股价、股本、一致预期一律不用）；
  2. 每条结果都带 basis（口径说明），可溯源、可复核；
  3. name / unit / basis 文本一律不含阿拉伯数字——避免公式里的常数混进
     `src.inputs.collect_allowed_numbers` 的数字白名单，等于给模型编造数字开后门；
  4. 输入缺失**不抛异常**：对应指标直接缺席，并在 warnings 里写明原因。

与 src/derived.py 的分工：
  - derived 侧重"给模型写话用的展示值"（亿元换算、情景测算外推）；
  - 本模块侧重"分析视角的比率/趋势/结构"，输出 group 分组，供报告「财务比率分析」章节
    与 gen_commentary 的上下文消费。二者同 code 的指标由调用方按 code 去重。
"""
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")   # 时点：YYYY-MM-DD

GROUP_PROFIT = "profitability"
GROUP_EXPENSE = "expense"
GROUP_TREND = "trend"
GROUP_STRUCT = "structure"
GROUP_ZH = {GROUP_PROFIT: "盈利能力", GROUP_EXPENSE: "费用率",
            GROUP_TREND: "趋势", GROUP_STRUCT: "结构"}


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
        q = abs(q)                      # 消除 -0.0 这种写法
    return format(q, "f")


def _metric(group: str, code: str, name: str, period, d: Decimal,
            nd: int, unit: str, basis: str) -> dict:
    return {"group": group, "code": code, "name": name, "period": str(period or ""),
            "value": _num(d, nd), "unit": unit, "basis": basis}


def _index(records) -> tuple[dict, dict]:
    """→ ({(code, 年份): 金额}, {(code, 期末日): 金额})。

    期间判别按**完整日期**（YYYY-MM-DD）而非"含连字符"：
    半年报的流量期间是 `2026-H1`（含连字符但不是时点），若按含连字符判定会被
    误当成时点数，导致半年报的同比/比率全部算不出。
    """
    fy: dict = {}
    bs: dict = {}
    if not isinstance(records, list):
        return fy, bs
    for r in records:
        if not isinstance(r, dict):
            continue
        code = r.get("item_code")
        period = str(r.get("period") or "")
        d = _dec(r.get("value"))
        y = _year(period)
        if not code or d is None or y is None:
            continue
        if _DATE_RE.match(period):
            bs[(code, period)] = d
        else:
            fy[(code, y)] = d
    return fy, bs


def _latest(fy: dict, bs: dict):
    years = sorted({y for _c, y in fy})
    dates = sorted({d for _c, d in bs})
    return years, dates


def _profitability(fy: dict, bs: dict, warnings: list[str]) -> list[dict]:
    """盈利能力：毛利率 / 净利率 / ROE / ROIC。"""
    out: list[dict] = []
    years, dates = _latest(fy, bs)
    y = years[-1] if years else None
    d = dates[-1] if dates else None

    rev = fy.get(("REVENUE", y)) if y else None
    cost = fy.get(("OPERATING_COST", y)) if y else None
    if y is not None and rev not in (None, 0) and cost is not None:
        out.append(_metric(GROUP_PROFIT, "RATIO_GROSS", "毛利率", y,
                           (rev - cost) / rev * 100, 1, "%",
                           "口径：毛利率＝（营业收入－营业成本）÷营业收入，以百分比列示。"))
    elif y is not None:
        warnings.append("缺少营业收入或营业成本，未计算毛利率")

    tr = fy.get(("TOTAL_REVENUE", y)) if y else None
    np_ = fy.get(("NET_PROFIT", y)) if y else None
    if y is not None and tr not in (None, 0) and np_ is not None:
        out.append(_metric(GROUP_PROFIT, "RATIO_NET", "净利率", y,
                           np_ / tr * 100, 1, "%",
                           "口径：净利率＝净利润÷营业总收入，以百分比列示。"))
    elif y is not None:
        warnings.append("缺少营业总收入或净利润，未计算净利率")

    npp = fy.get(("NP_PARENT", y)) if y else None
    eq = bs.get(("EQUITY_PARENT", d)) if d else None
    if npp is not None and eq not in (None, 0):
        out.append(_metric(GROUP_PROFIT, "ROE", "净资产收益率", d,
                           npp / eq * 100, 1, "%",
                           "口径：净资产收益率＝归母净利润÷期末归母所有者权益，以百分比列示。"))
    elif y is not None:
        warnings.append("缺少归母净利润或期末归母所有者权益，未计算净资产收益率")

    # ROIC：息税前利润按"利润总额＋财务费用"、实际税率按"所得税费用÷利润总额"，
    #   投入资本按"短期借款＋长期借款＋所有者权益期末"。任一分量缺失即不产出。
    tp = fy.get(("TOTAL_PROFIT", y)) if y else None
    fin = fy.get(("FIN_EXPENSE", y)) if y else None
    tax = fy.get(("INCOME_TAX", y)) if y else None
    if y is not None and d is not None and tp not in (None, 0) and fin is not None \
            and tax is not None:
        ebit = tp + fin
        rate = tax / tp
        nopat = ebit * (Decimal(1) - rate)
        sl = bs.get(("SHORT_TERM_LOAN", d)) or Decimal(0)
        ll = bs.get(("LONG_TERM_LOAN", d)) or Decimal(0)
        te = bs.get(("TOTAL_EQUITY", d))
        if te is not None and (sl + ll + te) != 0:
            out.append(_metric(GROUP_PROFIT, "ROIC", "投入资本回报率", d,
                               nopat / (sl + ll + te) * 100, 1, "%",
                               "口径：投入资本回报率＝息税前利润×(一－实际税率)÷"
                               "（短期借款＋长期借款＋所有者权益），以百分比列示。"))
        else:
            warnings.append("缺少期末所有者权益或投入资本为零，未计算投入资本回报率")
    elif y is not None:
        warnings.append("缺少利润总额、财务费用或所得税费用，未计算投入资本回报率")
    return out


_EXP_SRC = (("EXP_TAX_RATE", "TAX_SURCHARGE", "税金及附加率"),
            ("EXP_SELLING_RATE", "SELLING_EXPENSE", "销售费用率"),
            ("EXP_ADMIN_RATE", "ADMIN_EXPENSE", "管理费用率"))


def _expense(fy: dict, warnings: list[str]) -> list[dict]:
    """费用率：税金及附加率 / 销售费用率 / 管理费用率（分母均为营业总收入）。"""
    out: list[dict] = []
    years, _ = _latest(fy, {})
    y = years[-1] if years else None
    tr = fy.get(("TOTAL_REVENUE", y)) if y else None
    if y is None:
        warnings.append("没有利润表期间数据，未计算费用率")
        return out
    for code, src, name in _EXP_SRC:
        v = fy.get((src, y))
        if tr not in (None, 0) and v is not None:
            out.append(_metric(GROUP_EXPENSE, code, name, y, v / tr * 100, 1, "%",
                               f"口径：{name}＝{name[:-1] if name.endswith('率') else name}÷营业总收入，以百分比列示。"))
        else:
            warnings.append(f"缺少营业总收入或{name[:-1] if name.endswith('率') else name}，未计算{name}")
    return out


_TREND_FY = (("TOTAL_REVENUE", "营业总收入"), ("REVENUE", "营业收入"),
             ("NP_PARENT", "归母净利润"), ("NET_PROFIT", "净利润"))


def _trend(fy: dict, bs: dict, warnings: list[str]) -> list[dict]:
    """趋势：核心科目同比增速 + 比率同比变动（pcts）。"""
    out: list[dict] = []
    years, _ = _latest(fy, bs)
    if len(years) < 2:
        warnings.append("只有一期数据，未计算同比增速与比率同比变动")
        return out
    y, p = years[-1], years[-2]
    for code, name in _TREND_FY:
        cur, pre = fy.get((code, y)), fy.get((code, p))
        if cur is not None and pre not in (None, 0):
            out.append(_metric(GROUP_TREND, f"YOY_{code}", f"{name}同比增速", y,
                               (cur - pre) / pre * 100, 2, "%",
                               "口径：同比＝（本期－上期）÷上期，以百分比列示；上期取同一定期报告的比较期。"))
    # 比率同比变动（pcts）：毛利率、净利率
    for code, name in (("RATIO_GROSS", "毛利率"), ("RATIO_NET", "净利率")):
        out.append(_pct_chg(fy, y, p, code, name))
    return [m for m in out if m]


def _pct_chg(fy: dict, y: int, p: int, ratio_code: str, name: str):
    """比率同比变动：需要两期构成比率的原始科目都在。"""
    if ratio_code == "RATIO_GROSS":
        cur = _ratio_gross(fy, y)
        pre = _ratio_gross(fy, p)
    else:
        cur = _ratio_net(fy, y)
        pre = _ratio_net(fy, p)
    if cur is None or pre is None:
        return None
    return _metric(GROUP_TREND, f"PCTCHG_{ratio_code}", f"{name}同比变动", y,
                   cur - pre, 1, "pcts",
                   "口径：比率同比变动＝本期比率－上期比率，以百分点列示。")


def _ratio_gross(fy: dict, y: int) -> Decimal | None:
    rev, cost = fy.get(("REVENUE", y)), fy.get(("OPERATING_COST", y))
    return (rev - cost) / rev * 100 if rev not in (None, 0) and cost is not None else None


def _ratio_net(fy: dict, y: int) -> Decimal | None:
    tr, np_ = fy.get(("TOTAL_REVENUE", y)), fy.get(("NET_PROFIT", y))
    return np_ / tr * 100 if tr not in (None, 0) and np_ is not None else None


# 结构：分产品 / 分渠道占比（分母为分部口径的"酒类营业收入"——分部小计不含其他业务收入）
_MIX = (
    ("MIX_PRODUCT_MOUTAI", "REVENUE_MOUTAI", "茅台酒收入占比"),
    ("MIX_PRODUCT_SERIES", "REVENUE_SERIES", "系列酒收入占比"),
    ("MIX_CHANNEL_DIRECT", "REVENUE_DIRECT", "直销收入占比"),
    ("MIX_CHANNEL_WHOLESALE", "REVENUE_WHOLESALE", "批发代理收入占比"),
    ("MIX_CHANNEL_IMOUTAI", "REVENUE_IMOUTAI", "i茅台收入占比"),
)
# 吨价：分部收入 ÷ 同口径销量
_TON_PRICE = (("TON_PRICE_MOUTAI", "REVENUE_MOUTAI", "VOLUME_MOUTAI", "茅台酒吨价"),
              ("TON_PRICE_SERIES", "REVENUE_SERIES", "VOLUME_SERIES", "系列酒吨价"))


def _structure(fy: dict, warnings: list[str]) -> list[dict]:
    """结构：分产品/分渠道占比 + 吨价（分母缺失即不产出）。"""
    out: list[dict] = []
    years, _ = _latest(fy, {})
    y = years[-1] if years else None
    if y is None:
        warnings.append("没有期间数据，未计算结构占比")
        return out
    base = fy.get(("REVENUE_LIQUOR", y))
    if base not in (None, 0):
        for code, src, name in _MIX:
            v = fy.get((src, y))
            if v is None:
                continue
            out.append(_metric(GROUP_STRUCT, code, name, y, v / base * 100, 1, "%",
                               "口径：占比＝该项分部收入÷酒类营业收入，以百分比列示。"))
    else:
        warnings.append("缺少酒类营业收入，未计算分产品/分渠道占比")
    for code, rev_c, vol_c, name in _TON_PRICE:
        rev, vol = fy.get((rev_c, y)), fy.get((vol_c, y))
        if rev is not None and vol not in (None, 0):
            out.append(_metric(GROUP_STRUCT, code, name, y, rev / vol, 2, "元/吨",
                               "口径：吨价＝该项分部收入÷同口径销量，以元每吨列示。"))
        elif rev is not None:
            warnings.append(f"缺少{name}对应的销量，未计算吨价")
    return out


def analyze(records) -> dict:
    """入口：把结构化记录算成四组分析指标。

    → {"metrics": [{group, code, name, period, value, unit, basis}, ...],
       "warnings": ["…缺失…，未计算…", ...]}

    输入非法或为空时**不抛异常**，返回空 metrics 并在 warnings 说明原因。
    """
    if not isinstance(records, list) or not records:
        return {"metrics": [], "warnings": ["输入不是非空的记录列表，未做任何计算"]}
    fy, bs = _index(records)
    if not fy and not bs:
        return {"metrics": [], "warnings": ["记录里没有可解析的数值（缺少 item_code/period/value），未做任何计算"]}
    warnings: list[str] = []
    metrics: list[dict] = []
    try:
        metrics += _profitability(fy, bs, warnings)
        metrics += _expense(fy, warnings)
        metrics += _trend(fy, bs, warnings)
        metrics += _structure(fy, warnings)
    except (ArithmeticError, TypeError, ValueError) as exc:   # noqa: BLE001
        # 兜底：任何意外算术/类型错误都转成警告，绝不让分析模块把整条流水线带崩。
        return {"metrics": metrics, "warnings": warnings + [f"分析过程出现异常，已跳过：{type(exc).__name__}"]}
    return {"metrics": metrics, "warnings": warnings}


def group_metrics(metrics: list[dict]) -> dict[str, list[dict]]:
    """按 group 归类，供报告分节渲染。缺失的分组返回空列表。"""
    out = {g: [] for g in GROUP_ZH}
    for m in metrics or []:
        out.setdefault(m.get("group", ""), []).append(m)
    return out
