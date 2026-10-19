# src/derived.py
"""确定性派生计算（程序算数、模型写话）。

输入：B 阶段已核定的结构化记录 [{"item_code","period","value"}]。
输出：带"口径说明"的派生指标清单，供 gen_commentary 写研报式评价时引用。

三条铁律：
 1. 只做加/减/乘/除，不引入任何外部数据（股价、股本、一致预期一律不用）；
 2. 每个派生值都带 basis（口径说明），可溯源、可复核；
 3. basis / name 文本**一律不含阿拉伯数字**——否则公式串里的常数会混进
    collect_allowed_numbers 白名单，等于给模型开了编造数字的后门。
"""
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

YI = Decimal("100000000")        # 元 → 亿元
MAX_DERIVED = 40                 # 派生条目上限，控制提示词篇幅
# 时点期间判据：完整日期 YYYY-MM-DD。★ 不能只看"含连字符"——半年报的流量期间
# 是 `2026-H1`（含连字符但属时期数），按连字符判定会被误当成时点数，同比/比率全失效。
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 参与派生计算的科目：利润表/现金流量表类（按年）+ 资产负债表类（按时点）
SCALE_FY = (("TOTAL_REVENUE", "营业总收入"), ("REVENUE", "营业收入"),
            ("OPERATING_COST", "营业成本"), ("OPERATING_PROFIT", "营业利润"),
            ("NET_PROFIT", "净利润"), ("NP_PARENT", "归母净利润"),
            ("CF_OPERATING", "经营活动现金流量净额"))
SCALE_BS = (("TOTAL_ASSETS", "资产总计"), ("TOTAL_LIAB", "负债合计"),
            ("TOTAL_EQUITY", "所有者权益合计"))

# 比率口径：(键, 显示名, 单位, 小数位, 口径说明)
RATIO_META = (
    ("RATIO_GROSS", "毛利率", "%", 1,
     "口径：毛利率＝（营业收入－营业成本）÷营业收入，以百分比列示。"),
    ("RATIO_NET", "净利率", "%", 1,
     "口径：净利率＝净利润÷营业总收入，以百分比列示。"),
    ("RATIO_NPP", "归母净利率", "%", 1,
     "口径：归母净利率＝归母净利润÷营业总收入，以百分比列示。"),
    ("RATIO_OP", "营业利润率", "%", 1,
     "口径：营业利润率＝营业利润÷营业总收入，以百分比列示。"),
    ("RATIO_CASH", "经营现金流净利润比", "倍", 2,
     "口径：经营活动现金流量净额÷净利润，以倍数列示。"),
    ("RATIO_LEV", "资产负债率", "%", 1,
     "口径：资产负债率＝负债合计÷资产总计，以百分比列示。"),
)
# 需要给出"同比变动（pcts）"的比率
PCT_KEYS = ("RATIO_GROSS", "RATIO_NET", "RATIO_NPP", "RATIO_OP", "RATIO_LEV")
PCT_META = {k: (n, u) for k, n, u, _d, _b in RATIO_META}


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


def _item(code: str, name: str, period: str, value: str,
          unit: str, basis: str) -> dict:
    return {"code": code, "name": name, "period": period,
            "value": value, "unit": unit, "basis": basis}


def _index(records: list[dict]) -> tuple[dict, dict]:
    """拆成两张表：(科目, 年份)→金额（时期数）、(科目, 期末日)→金额（时点数）。"""
    fy, bs = {}, {}
    for r in records:
        code, period, d = r.get("item_code"), str(r.get("period") or ""), None
        if not code:
            continue
        d = _dec(r.get("value"))
        y = _year(period)
        if d is None or y is None:
            continue
        if _DATE_RE.match(period):              # 2025-12-31 → 时点
            bs[(code, period)] = d
        else:                                   # 2025 / 2026-H1 → 时期
            fy[(code, y)] = d
    return fy, bs


def _ratios(fy: dict, bs: dict, year: int | None, date: str | None) -> dict:
    """按给定年份/期末日算一组比率；数据不全的项直接不产出（不猜、不补）。"""
    r = {}
    if year is not None:
        rev = fy.get(("REVENUE", year))
        cost = fy.get(("OPERATING_COST", year))
        tr = fy.get(("TOTAL_REVENUE", year))
        np_ = fy.get(("NET_PROFIT", year))
        npp = fy.get(("NP_PARENT", year))
        op = fy.get(("OPERATING_PROFIT", year))
        cf = fy.get(("CF_OPERATING", year))
        if rev not in (None, 0) and cost is not None:
            r["RATIO_GROSS"] = (rev - cost) / rev * 100
        if tr not in (None, 0) and np_ is not None:
            r["RATIO_NET"] = np_ / tr * 100
        if tr not in (None, 0) and npp is not None:
            r["RATIO_NPP"] = npp / tr * 100
        if tr not in (None, 0) and op is not None:
            r["RATIO_OP"] = op / tr * 100
        if np_ not in (None, 0) and cf is not None:
            r["RATIO_CASH"] = cf / np_
    if date is not None:
        ta = bs.get(("TOTAL_ASSETS", date))
        tl = bs.get(("TOTAL_LIAB", date))
        if ta not in (None, 0) and tl is not None:
            r["RATIO_LEV"] = tl / ta * 100
    return r


# 供 C研 研报引用比对消费的比率：真值由程序计算，item_code 与 fields.yaml 对齐
RATIO_CODES = ("RATIO_GROSS", "RATIO_NET", "RATIO_LEV")


def ratio_records(records: list[dict]) -> list[dict]:
    """把可由主表确定性计算的比率，产成**逐期记录**，供研报引用数值比对。

    与 compute_derived 的区别：后者只取"最近一期"给模型写话；本函数对**每个期间**
    各产一条，因为研报可能引用上年或更早期间。口径与 RATIO_META 完全一致，
    数值仍由程序算，模型不参与——只是把结果整理成与结构化记录同构的形态。
    """
    fy, bs = _index(records)
    meta = {k: (n, u, nd) for k, n, u, nd, _b in RATIO_META}
    out: list[dict] = []
    periods = [(str(y), "FY", _ratios(fy, bs, y, None)) for y in sorted({y for _c, y in fy})]
    periods += [(d, "BAL_END", _ratios(fy, bs, None, d)) for d in sorted({d for _c, d in bs})]
    for period, ptype, vals in periods:
        for key in RATIO_CODES:
            if key not in vals:
                continue
            name, unit, nd = meta[key]
            out.append({"item_code": key, "period": period, "period_type": ptype,
                        "value": _num(vals[key], nd), "unit_raw": unit,
                        "raw_text": f"{name}{_num(vals[key], nd)}{unit}"})
    return out


def compute_derived(records: list[dict]) -> list[dict]:
    """产出派生指标清单；输入不足时对应条目自动缺席（模型不得因此编数）。"""
    fy, bs = _index(records)
    fy_years = sorted({y for _c, y in fy})
    bs_dates = sorted({d for _c, d in bs})
    if not fy_years and not bs_dates:
        return []

    latest_fy = fy_years[-1] if fy_years else None
    prev_fy = fy_years[-2] if len(fy_years) > 1 else None
    latest_bs = bs_dates[-1] if bs_dates else None
    prev_bs = bs_dates[-2] if len(bs_dates) > 1 else None

    out: list[dict] = []

    # ---- ① 亿元口径换算（正文用亿元表述，对齐研报） ----
    scale_basis = "口径：按合并报表披露值由元换算为亿元，保留一位小数。"
    for code, name in SCALE_FY:
        v = fy.get((code, latest_fy)) if latest_fy is not None else None
        if v is not None:
            out.append(_item(f"SCALE_{code}", f"{name}（亿元）", str(latest_fy),
                             _num(v / YI, 1), "亿元", scale_basis))
    for code, name in SCALE_BS:
        v = bs.get((code, latest_bs)) if latest_bs is not None else None
        if v is not None:
            out.append(_item(f"SCALE_{code}", f"{name}（亿元）", str(latest_bs),
                             _num(v / YI, 1), "亿元", scale_basis))

    # ---- ② 同比增速（本期 vs 上期，单位 %） ----
    yoy_basis = "口径：同比＝（本期－上期）÷上期，以百分比列示；上期取自同一定期报告披露的比较期数据。"
    for code, name in SCALE_FY:
        cur = fy.get((code, latest_fy)) if latest_fy is not None else None
        pre = fy.get((code, prev_fy)) if prev_fy is not None else None
        if cur is not None and pre not in (None, 0):
            out.append(_item(f"YOY_{code}", f"{name}同比增速", str(latest_fy),
                             _num((cur - pre) / pre * 100, 2), "%", yoy_basis))
    for code, name in SCALE_BS:
        cur = bs.get((code, latest_bs)) if latest_bs is not None else None
        pre = bs.get((code, prev_bs)) if prev_bs is not None else None
        if cur is not None and pre not in (None, 0):
            out.append(_item(f"YOY_{code}", f"{name}同比增速", str(latest_bs),
                             _num((cur - pre) / pre * 100, 2), "%", yoy_basis))

    # ---- ③ 比率（本期） + ④ 比率同比变动（pcts，需上期比率） ----
    cur_ratio = _ratios(fy, bs, latest_fy, latest_bs)
    pre_ratio = _ratios(fy, bs, prev_fy, prev_bs)
    pct_basis = "口径：比率同比变动＝本期比率－上期比率，以百分点列示。"
    for key, name, unit, nd, basis in RATIO_META:
        if key in cur_ratio:
            out.append(_item(key, name, str(latest_fy if key != "RATIO_LEV" else latest_bs),
                             _num(cur_ratio[key], nd), unit, basis))
            if key in PCT_KEYS and key in pre_ratio:
                out.append(_item(f"PCTCHG_{key}", f"{name}同比变动",
                                 str(latest_fy), _num(cur_ratio[key] - pre_ratio[key], 1),
                                 "pcts", pct_basis))

    # ---- ⑤ 情景测算外推（按最近一期同比增速，明确标注非盈利预测） ----
    proj_basis = ("口径：按最近一期同比增速线性外推的测算值，非盈利预测，"
                  "不含分析师预期，仅供情景参考。")
    if latest_fy is not None and prev_fy is not None:
        for code, name in (("TOTAL_REVENUE", "营业总收入"), ("NP_PARENT", "归母净利润")):
            cur = fy.get((code, latest_fy))
            pre = fy.get((code, prev_fy))
            if cur is None or pre in (None, 0):
                continue
            g = (cur - pre) / pre
            for step in (1, 2, 3):
                year = latest_fy + step
                out.append(_item(f"PROJ_{code}_{year}", f"{name}测算值", f"{year}E",
                                 _num(cur * (1 + g) ** step / YI, 1), "亿元", proj_basis))

    return out[:MAX_DERIVED]
