# src/research_check.py
"""C研 维度：研报引用核查的**确定性计算**部分。

职责划分（对应 interfaces.md 的技术路线）：
  - 模型（match_claim）只回答"这句话在说哪个科目、哪个期间"；
  - 本模块负责"这句话里的数值和财报对不对得上"——数值一律由程序产生与比较。

★ 为什么不让模型直接给出句子里的金额：
  interfaces.md §5.3 禁则(1)「模型不产出数值」。金额取自研报原文（程序自己持有），
  关联关系由"指标名 → 其后最近的金额"这一确定性规则建立，可复核、可复现。

产出格式遵循 interfaces.md §4.1（rule results），供 CSI 评分与报告侧消费。
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

RULE_ID = "RC01@v1.0"          # 研报引用一致性：一条规则，多个实例（每条 claim 一例）
YOY_RULE_ID = "RC02@v1.0"      # 研报引用同比核验：同上，只在不符时产出结果
TOLERANCE_PROFILE = "REPORT_REF"

# 金额+单位 → 换算到"元"的乘数。键为**合法**单位写法。
UNIT_SCALE = {
    "万亿": Decimal("1000000000000"),
    "亿元": Decimal("100000000"),
    "亿":   Decimal("100000000"),
    "百万元": Decimal("1000000"),
    "万元": Decimal("10000"),
    "万":   Decimal("10000"),
    "千元": Decimal("1000"),
    "千":   Decimal("1000"),
    "元":   Decimal("1"),
}
# 单位整体捕获（可含多个量级字，如"亿万元"），再校验是否合法。
# 排除"万吨/万股"等非金额单位：负向先行断言挡掉，避免把销量当营收。
NUM_RE = re.compile(
    r"(-?)\s*(\d[\d,]*(?:\.\d+)?)\s*([万亿千百]*元|[万亿])(?![吨股人件])")

# 研报口语化指标名 → 标准科目编号（fields.yaml 的 zh 为正式名，此处补研报简写）。
# ★ 刻意不收"营收""总收入"这类过于泛化的词：研报里
#     "茅台酒 2024 年营收 1495.3 亿元"
#   中的"营收"是普通名词而非指标名，收进来会把**分部营收**误挂到**总营收**上，
#   造成用 1495.3 亿去比 1741.4 亿的误报。宁可少匹配，也不误报。
ALIASES = {
    "营业总收入": "TOTAL_REVENUE",
    "营业收入": "REVENUE",
    "归母净利润": "NP_PARENT", "归母净利": "NP_PARENT",
    "归属于母公司股东的净利润": "NP_PARENT", "归属于母公司所有者的净利润": "NP_PARENT",
    "净利润": "NET_PROFIT",
    "营业成本": "OPERATING_COST",
    "营业利润": "OPERATING_PROFIT",
    "利润总额": "TOTAL_PROFIT",
    "所得税费用": "INCOME_TAX",
    "资产总计": "TOTAL_ASSETS", "总资产": "TOTAL_ASSETS",
    "负债合计": "TOTAL_LIAB",
    "所有者权益合计": "TOTAL_EQUITY",
    "货币资金": "CASH_END",
    "应收账款": "AR_NET",
    "存货": "INVENTORY",
    "固定资产": "FIXED_ASSETS",
    "茅台酒": "REVENUE_MOUTAI",
    "系列酒": "REVENUE_SERIES",
    "经营活动产生的现金流量净额": "CF_OPERATING",
    # v1.2：研报高频指标——补充口语化写法（正式名已由 fields.yaml 的 zh 覆盖）
    "毛利率": "RATIO_GROSS", "净利率": "RATIO_NET", "资产负债率": "RATIO_LEV",
    "销售费用": "SELLING_EXPENSE", "管理费用": "ADMIN_EXPENSE",
    "研发费用": "RD_EXPENSE", "财务费用": "FIN_EXPENSE", "税金及附加": "TAX_SURCHARGE",
    "合同负债": "CONTRACT_LIAB", "应付账款": "AP",
    "短期借款": "SHORT_TERM_LOAN", "长期借款": "LONG_TERM_LOAN",
}

# 比率类指标：真值由程序计算（src.derived.ratio_records），句中数值以百分号为单位，
# 用专门的百分比解析器归属——与金额解析器分开，避免"增长8%"被误挂到金额科目上。
RATIO_CODES = {"RATIO_GROSS", "RATIO_NET", "RATIO_LEV"}
PCT_RE = re.compile(r"(-?)\s*(\d[\d,]*(?:\.\d+)?)\s*%")

# 净利率口径互认：研报口语里的"净利率"经常写的是**归母净利率**（茅台 2025 即如此：
# 研报 47.8% 实为归母口径，而程序真值 NET_PROFIT/TOTAL_REVENUE = 49.6%）。
# 两个口径都由程序从同一批结构化数据算出。主口径判不一致时，用另一口径复核一次：
# 落在容差内即视为**列报口径不同**而非引用错误，避免拿两个口径互相扣分。
MARGIN_ALT = {"RATIO_NET": "RATIO_NPP", "RATIO_NPP": "RATIO_NET"}
MARGIN_PARTS = {"RATIO_NET": ("NET_PROFIT", "TOTAL_REVENUE"),
                "RATIO_NPP": ("NP_PARENT", "TOTAL_REVENUE")}
MARGIN_ZH = {"RATIO_NET": "净利率（净利润÷营业总收入）",
             "RATIO_NPP": "归母净利率（归母净利润÷营业总收入）"}

# 金额与它的"主语指标名"之间允许的最大字符距离。
# 没有这个约束，"吨价 314.4 万元"会被挂到远在句首的"茅台酒"上，又变误报。
MAX_NAME_GAP = 20


def _load_fields() -> dict:
    return yaml.safe_load((ROOT / "rules" / "fields.yaml").read_text(encoding="utf-8"))["fields"]


def _load_tolerance() -> dict:
    return yaml.safe_load(
        (ROOT / "rules" / "tolerance.yaml").read_text(encoding="utf-8"))


def _index_records(records: list[dict]) -> dict:
    """(item_code, period) -> 记录。同一键多条时保留全部（跨口径不混是一致性前提）。"""
    idx: dict[tuple[str, str], list[dict]] = {}
    for r in records:
        idx.setdefault((r["item_code"], r["period"]), []).append(r)
    return idx


def margin_alt_truth(code: str, period: str, idx: dict) -> Decimal | None:
    """算出同一期间的**另一净利率口径**真值（见 MARGIN_ALT）；不适用或数据不全返回 None。

    真值同样由程序从结构化数据算，不让模型参与；精度与 derived 一致（百分数保留一位小数）。
    """
    alt = MARGIN_ALT.get(code)
    parts = MARGIN_PARTS.get(alt) if alt else None
    if not parts:
        return None
    num_recs, den_recs = idx.get((parts[0], period)), idx.get((parts[1], period))
    if not num_recs or not den_recs:
        return None
    try:
        num = Decimal(str(num_recs[0]["value"]).replace(",", "").strip())
        den = Decimal(str(den_recs[0]["value"]).replace(",", "").strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    if den == 0:
        return None
    return (num / den * 100).quantize(Decimal("0.1"))


def _fy_index(records: list[dict]) -> dict:
    """(item_code, 年份) -> 数值：只收期间为纯 4 位年份的时期数记录，供同比基准使用。

    刻意不收时点（2025-12-31）：时点科目没有"同比"这一说，混进来会算出错误的基准。
    """
    out: dict[tuple[str, int], object] = {}
    for r in records:
        m = re.match(r"^(20\d{2})$", str(r.get("period") or ""))
        if m:
            out[(r["item_code"], int(m.group(1)))] = r.get("value")
    return out


def yoy_truth(code: str, period: str, fy: dict) -> Decimal | None:
    """财报口径同比：(本期−上期)÷|上期|×100。缺上期或分母为零返回 None——不猜、不补。

    只认**纯年份**期间：`2025-H1` / `2025-12-31` 的上期基数不是上一个年度数
    （前者应比 2024-H1、后者是时点数），拿年度数去算会把基期算错，故一律不产出。
    """
    m = re.match(r"^(20\d{2})$", str(period or ""))
    if not m:
        return None
    y = int(m.group(1))
    cur, prev = fy.get((code, y)), fy.get((code, y - 1))
    if cur is None or prev is None:
        return None
    try:
        c = Decimal(str(cur).replace(",", "").strip())
        p = Decimal(str(prev).replace(",", "").strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    if p == 0:
        return None
    return ((c - p) / abs(p) * 100).quantize(Decimal("0.01"))


# 同比容差单列：研报的增速常只保留一位小数（真值 -4.53% 写成 -4.5%），
# 沿用金额的 rel 档位会把这种"两位小数vs一位小数"的四舍五入差异判成异常。
YOY_ABS_TOL = Decimal("0.1")
YOY_REL_TOL = Decimal("0.05")


def yoy_tolerance(truth: Decimal) -> Decimal:
    return max(YOY_ABS_TOL, YOY_REL_TOL * abs(truth))


PERIOD_YEAR_RE = re.compile(r"(20\d{2})\s*年")
# 同比方向既可能写成符号（同比+1.2% / 同比-1.2%），也可能写成中文词
# （同比增加1.2% / 同比减少1.2%）。只认符号会把中文写法整类漏掉。
YOY_DOWN_WORDS = ("减少", "下降", "降低", "下滑", "回落")
YOY_UP_WORDS = ("增加", "增长", "上升", "提升")
YOY_TOKEN_RE = re.compile(
    r"(?:同比\s*)?(?:"
    r"([+\-])\s*(\d[\d,]*(?:\.\d+)?)"
    r"|(" + "|".join(YOY_DOWN_WORDS + YOY_UP_WORDS) + r")\s*(\d[\d,]*(?:\.\d+)?)"
    r")\s*%")
YOY_WINDOW = 15               # "同比±X%"必须紧跟在数值之后，超出窗口就不认（避免挂到别的指标）
# 金额枚举的"前一项"形态：裸数字（无单位）后紧跟斜杠，如「847/904/982 亿元」里的 904/
ENUM_LIST_BEFORE_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*/\s*$")
# 百分数序列的"后一项"形态：紧随「/数字」，如「+2.8%/+6.7%/+8.6%」里的 +2.8%
ENUM_PCT_AFTER_RE = re.compile(r"/\s*[-+]?\d")


def period_year_before(sentence: str, fig: dict) -> str | None:
    """取指标名**之前**最近的一个"20XX年"，作为该数值的期间修饰；没有则返回 None。

    「截至 2026年6月末合同负债 80.1 亿元」的期间修饰是 2026，而数值匹配的是 2025-12-31
    ——正是靠这一步才能把"只写错期间、金额没错"的引用抓出来。
    """
    pos = fig.get("name_pos")
    if pos is None:
        return None
    years = PERIOD_YEAR_RE.findall(sentence[:pos])
    return years[-1] if years else None


def _enum_before(sentence: str, start: int) -> bool:
    """本值前面是否为「裸数字 + /」——金额枚举的前一项（如「847/904/982 亿元」的 904/）。

    金额枚举被抽取成一行时，只有**末位**那一个能带上单位（NUM_RE 要求数字后紧跟单位），
    故判据只需看它的前面：裸数字直接以斜杠分隔、中间没有单位，才是同一串枚举。
    """
    return bool(ENUM_LIST_BEFORE_RE.search(sentence[:start]))


def _enum_pct_after(sentence: str, end: int) -> bool:
    """本值后面是否紧跟「/数字」——百分数序列的后一项（如「+2.8%/+6.7%/+8.6%」）。

    ★ 只用于**百分数**（比率/同比）：百分数序列里的下一项必然是数字，跟随的斜杠只可能
      是枚举分隔。金额绝不能这么判——`茅台酒2025 年收入1465 亿元/+0.39%` 里的斜杠后面
      跟的是**同比**（也是百分数），据此判枚举会把每次"金额/同比"的正确引用都误转复核，
      连带把真异常（系列酒 232 亿元）也降级掉。
    """
    return bool(ENUM_PCT_AFTER_RE.match(sentence[end:]))


def yoy_after(sentence: str, fig: dict) -> tuple[Decimal, str, bool] | None:
    """取数值之后紧跟的"同比 ±X%"（窗口内）→ (带符号的百分数, 原串, 是否多值枚举)。

    原串从"同比/方向词"起算，**不含**其前导的逗号或斜杠（此前用 `seg[:m.end()]`，
    会把分隔符一起带出来，报告里显示成「/同比+1.2%」）。没有则返回 None。
    """
    end = fig.get("num_end")
    if end is None:
        return None
    seg = sentence[end:end + YOY_WINDOW]
    m = YOY_TOKEN_RE.search(seg)
    if not m:
        return None
    try:
        if m.group(1) is not None:                      # 符号写法：同比+1.2%
            val = Decimal(m.group(2).replace(",", ""))
            if m.group(1) == "-":
                val = -val
        else:                                           # 中文写法：同比减少1.2%
            val = Decimal(m.group(4).replace(",", ""))
            if m.group(3) in YOY_DOWN_WORDS:
                val = -val
    except InvalidOperation:
        return None
    enumerated = _enum_pct_after(sentence, end + m.end())
    return val, seg[m.start():m.end()], enumerated


def _display_names(fields: dict) -> dict:
    """标准科目编号 -> 中文正式名，用于在句子里定位指标。"""
    return {code: m.get("zh", code) for code, m in fields.items()}


QUARTER_RE = re.compile(r"Q[1-4]|第[一二三四]季度|上半年|中报|半年度")

# 预测/多列表格行：研报的盈利预测表被抽取器压成一行，列头年份在抽取时丢失，
# 典型形态是「毛利率 | 92.1% | 91.3% | 90.6% | 90.4% | 90.6%」。此时行内数值
# 与年份列无法对齐，拿"行内第一个数值"去比某一期财报必然张冠李戴——实测
# 92.1% 实为 2026E、22.0% 实为预测期，却被贴到 2025/2023/2024，凭空产出 4 条误报。
PIPE_RE = re.compile(r"\|")
NUM_TOKEN_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
YEAR_TOKEN_RE = re.compile(r"20\d{2}")
FORECAST_MARKER_RE = re.compile(r"预计|预测|展望|指引")


def is_forecast_row(sentence: str) -> bool:
    """判断句子是否为「无法对齐期间的预测行/多列表格行」。只用于把误报降级为复核。"""
    n = len(NUM_TOKEN_RE.findall(sentence))
    if len(PIPE_RE.findall(sentence)) >= 2 and n >= 3:
        return True                       # 表格行：整行同型数值，列头年份已丢失
    return (bool(FORECAST_MARKER_RE.search(sentence))
            and n >= 3
            and not YEAR_TOKEN_RE.search(sentence))   # 展望句：无年份可对齐


def _mentions(sentence: str, zh_map: dict) -> list[tuple[int, int, str]]:
    """找出句中所有指标名的出现区间 (start, end, item_code)，重叠时长名优先。"""
    cands = [(m.start(), m.end(), code)
             for name, code in ALIASES.items()
             for m in re.finditer(re.escape(name), sentence)]
    cands += [(m.start(), m.end(), code)
              for code, zh in zh_map.items()
              for m in re.finditer(re.escape(zh), sentence)]
    cands.sort(key=lambda x: (-(x[1] - x[0]), x[0]))
    chosen: list[tuple[int, int, str]] = []
    for s, e, code in cands:
        if any(not (e <= cs or s >= ce) for cs, ce, _ in chosen):
            continue                      # 与本区间重叠的短名丢弃
        chosen.append((s, e, code))
    return chosen


def associate_figures(sentence: str, zh_map: dict) -> dict[str, dict]:
    """把句中每个金额关联到**它前面最近的那个指标名**。

    为什么不能用"指标名之后第一个金额"：研报里常见
        "茅台酒 2024 年营收 1495.3 亿元"
    其中"茅台酒"与"营收"都在金额之前。若按"名字后第一个金额"，
    1495.3 亿会被同时算作 REVENUE，于是拿分部数去比总营收 —— 必然误报。
    按"最近的前置指标名"归属，1495.3 亿只归 REVENUE_MOUTAI。
    """
    mentions = _mentions(sentence, zh_map)
    out: dict[str, dict] = {}
    for m in NUM_RE.finditer(sentence):
        owner = None
        for s, e, code in mentions:
            if e <= m.start() and m.start() - e <= MAX_NAME_GAP:
                if owner is None or e > owner[1]:
                    owner = (s, e, code)
        if owner is None:
            continue
        try:
            val = Decimal(m.group(2).replace(",", ""))
        except InvalidOperation:
            continue
        if m.group(1) == "-":
            val = -val

        unit = m.group(3)
        if unit in UNIT_SCALE:
            value, bad_unit = val * UNIT_SCALE[unit], ""
        else:
            # 形如"亿万元"的畸形单位：按首个量级字估值以便比较，同时标记单位异常。
            # 用 .get 兜底——量级字表与 UNIT_SCALE 的键不保证完全对齐，
            # 直接索引会抛 KeyError 把整条流程打断（实测踩过 "千"）。
            head = next((u for u in ("万亿", "亿", "万", "千") if unit.startswith(u)), "元")
            value, bad_unit = val * UNIT_SCALE.get(head, Decimal("1")), unit

        # 同一指标在句中多次出现时，保留第一次（研报通常先给主口径）
        out.setdefault(owner[2], {
            "value": value,
            "raw": sentence[owner[0]:m.end()],
            "num_raw": m.group(2) + unit,
            "matched_name": sentence[owner[0]:owner[1]],
            "name_pos": owner[0],     # 指标名起点：供期间修饰短语比对（见 _period_year）
            "num_end": m.end(),       # 数值终点：供紧随其后的"同比±X%"定位
            "bad_unit": bad_unit,
            "enumerated": _enum_before(sentence, m.start()),
        })
    return out


def associate_percent(sentence: str, zh_map: dict) -> dict[str, dict]:
    """比率类指标专用：把句中的百分比关联到它**前面最近**的指标名。

    口径与 associate_figures 一致（同名归属规则、同样的最大间隔约束），
    只是金额正则换成百分号正则、不做单位量级换算——比率以 % 为唯一单位。
    """
    mentions = _mentions(sentence, zh_map)
    out: dict[str, dict] = {}
    for m in PCT_RE.finditer(sentence):
        owner = None
        for s, e, code in mentions:
            if e <= m.start() and m.start() - e <= MAX_NAME_GAP:
                if owner is None or e > owner[1]:
                    owner = (s, e, code)
        if owner is None:
            continue
        try:
            val = Decimal(m.group(2).replace(",", ""))
        except InvalidOperation:
            continue
        if m.group(1) == "-":
            val = -val
        out.setdefault(owner[2], {
            "value": val,
            "raw": sentence[owner[0]:m.end()],
            "num_raw": m.group(2) + "%",
            "matched_name": sentence[owner[0]:owner[1]],
            "name_pos": owner[0],
            "num_end": m.end(),
            "bad_unit": "",
            "enumerated": _enum_before(sentence, m.start()) or _enum_pct_after(sentence, m.end()),
        })
    return out


def tolerance_for(profile: dict, left: Decimal, right: Decimal) -> Decimal:
    """有效容差 = max(abs 下限, rel × max(|左|,|右|))；REPORT_REF 不按单位取下限。"""
    abs_floor = Decimal(str(profile.get("abs", "0")))
    rel = Decimal(str(profile.get("rel", "0")))
    return max(abs_floor, rel * max(abs(left), abs(right)))


def classify_diff(claim_val: Decimal, truth: Decimal) -> tuple[str, str]:
    """确定性归因：不做主观猜测，按顺序检查倍数/期间/符号。"""
    if truth == 0:
        return "VALUE_ERROR", "财报值为零，无法做倍数归因"
    ratio = claim_val / truth
    for exp in range(-12, 13):
        if exp == 0:
            continue
        if abs(ratio - (Decimal(10) ** exp)) < abs(Decimal(10) ** exp) * Decimal("0.001"):
            return "UNIT_SCALE", f"研报值 ≈ 财报值 × 10^{exp}"
    if claim_val == -truth:
        return "SIGN_ERROR", "研报值与财报值符号相反"
    return "VALUE_ERROR", f"研报值 / 财报值 = {ratio:.4f}，非 10 的整数次幂"


# ---------------------------------------------------------------------------
# 研报**文档内部**一致性检测器（RC03 / RC04 / RC05）
#
# 为什么需要它们：RC01/RC02 是"研报 vs 财报"的比对，一旦财报侧没有对应字段
# （分产品收入、合同负债、单季数据）或该条引用根本对不上任何科目，核查就会以
# "待人工复核"收尾——清单里植的"计算错误 / 单位错误 / 期间错误 / 同比方向错误"
# 大多属于这一类。这三个检测器只读研报自身（claim 里已带页码/段号/表行号），
# 因此**离线也必然可执行**，且完全不依赖模型输出，可复现、可逐条复核。
# ---------------------------------------------------------------------------
CALC_RULE_ID = "RC03@v1.0"      # 表内自校验：调整幅度列应与其数据列自洽
UNIT_RULE_ID = "RC04@v1.0"      # 单位口径：表行标签单位 vs 文档统一口径
INTRA_RULE_ID = "RC05@v1.0"     # 研报内一致性：预测年份/数值/方向 与表头表行对照

CALC_TOL = Decimal("0.15")      # 自校验容差：列报值保留一位小数时最大舍入误差 ≈0.05

# 数值 + 可选百分号/pcts 后缀（供表行按序取值）
TOKEN_RE = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*(%|pcts?)?")
# 表格行标签里的括号单位，如「净利润(千万元)」「营业收入（百万元）」
PAREN_UNIT_RE = re.compile(r"[（(]\s*([^）)]{0,8}?元)\s*[）)]")
# 报表行标签白名单：只有这些行的单位才与文档口径比较（「每股收益（元）」等排除在外）
STATEMENT_PREFIXES = (
    "营业总收入", "营业收入", "净利润", "归母净利润", "营业成本", "营业利润",
    "利润总额", "资产总计", "负债合计", "所有者权益合计", "货币资金", "应收账款",
    "存货", "固定资产", "在建工程", "短期借款", "长期借款", "应付账款", "合同负债",
    "经营活动产生的现金流量净额", "经营活动现金流量净额", "研发费用", "销售费用",
    "管理费用", "财务费用", "税金及附加",
)
# 只有"量级单位"参与口径比对：每股收益的"元"、比率类的"%"天然不同于报表口径
MAGNITUDE_UNITS = ("万亿", "亿", "亿元", "百万元", "千万元", "万元", "万元")
# 预测区间「2025-2027 年」（起点/终点年份）
RANGE_RE = re.compile(r"(20\d{2})\s*[-–—~～至]\s*(20\d{2})\s*年")
# 「20XX年…目标/指引/展望」（经营目标只会指向**尚未结束**的年度）
TARGET_AFTER_RE = re.compile(r"(20\d{2})\s*年[^。；，、！？]{0,10}?(?:目标|指引|展望)")
TARGET_BEFORE_RE = re.compile(r"(?:目标|指引|展望)[^。；，、！？]{0,10}?(20\d{2})\s*年")
# 「截至 20XX年X月末」：年报里出现**晚于报告年度**的时点即期间张冠李戴
CUTOFF_RE = re.compile(r"(20\d{2})\s*年\s*\d{1,2}\s*月[末底]?")
# 句内百分号序列（顺序保留），供 RC05(b)(c) 与表行对照
SERIES_PCT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*%")
# 「同比(增长|减少)X%/Y%/Z%」方向词 + 连续百分号序列
SIGN_SERIES_RE = re.compile(
    r"(增加|增长|上升|提升|减少|下降|降低|下滑|回落)\s*"
    r"((?:\d[\d,]*(?:\.\d+)?\s*%\s*[/、,和及]?\s*){2,})")


def _report_year(claim_id: str) -> int | None:
    """从 claim_id 里取报告所属年度（`r2024_p04_002` / `2025_xx::r2025_...`）。"""
    m = re.search(r"(20\d{2})", str(claim_id or ""))
    return int(m.group(1)) if m else None


def _claim_record(rule_id: str, claim: dict, cause: str, reason: str,
                  left: dict, right: dict, diff: str, expected: str = "—") -> dict:
    """检测器产出的结果条目：字段与 RC01 保持一致，另带**位置**（页码/段落/表行）。

    ★ expected = 「这一处本来应该是什么」的终值（正确单位写法、正确幅度、正确时点…）。
      报告四端把它单列一列「应为（正确值）」：只给"哪里错了"不够，复核的人还要知道
      "改成什么才对"，否则说明看不懂（实测：只写"千万元不属于认可的量级单位"，
      使用者无法判断该改成亿元还是百万元）。
    """
    return {
        "rule_id": rule_id, "dimension": "C研", "claim_id": claim.get("claim_id"),
        "item_code": None, "period": None, "status": "FAIL",
        "left": {"source": "research_report", **left},
        "right": {"source": "research_report", **right},
        "diff": diff, "tolerance": None, "severity": "HIGH",
        "cause": cause, "reason": reason, "expected": expected,
        "evidence": {
            "file": claim.get("source"), "section": claim.get("section"),
            "sentence": claim.get("sentence"), "page": claim.get("page"),
            "para_no": claim.get("para_no"), "row_no": claim.get("row_no"),
            "caption": claim.get("caption"), "verified": True,
        },
    }


def _tokens(sentence: str) -> list[tuple[Decimal, str]]:
    """表行按出现顺序取值 → [(数值, 单位标记)]；单位标记为 '%'/'pct' 或 ''。"""
    line = re.sub(r",\s+", ",", sentence or "")       # 「174, 144」→「174,144」
    out: list[tuple[Decimal, str]] = []
    for m in TOKEN_RE.finditer(line):
        try:
            out.append((Decimal(m.group(1).replace(",", "")), (m.group(2) or "").lower()))
        except InvalidOperation:
            continue
    return out


def _pct_row_values(claim: dict) -> list[Decimal] | None:
    """全百分号表行（每个数据格都是 X%）→ 数值序列；否则 None。

    只认"标签格 + 清一色百分号数据格"的行：预测调整表那种
    「营业收入（百万元）| … | 1.6% | 1.6%」的混合行不参与，避免把
    「调整幅度」当成长率去和正文的百分比序列对照。
    """
    if claim.get("kind") != "TABLE_ROW":
        return None
    cells = [c.strip() for c in str(claim.get("sentence") or "").split("|")]
    if len(cells) < 3:
        return None
    vals: list[Decimal] = []
    for c in cells[1:]:
        m = re.fullmatch(r"([+\-]?\d[\d,]*(?:\.\d+)?)\s*%", c)
        if not m:
            return None
        try:
            vals.append(Decimal(m.group(1).replace(",", "")))
        except InvalidOperation:
            return None
    return vals or None


def _selfcheck_row(claim: dict) -> tuple[str, str, str] | None:
    """RC03：调整表的"调整幅度"列是否与其数据列自洽 → (cause, reason, expected) 或 None。

    形态：`营业收入（百万元） | 旧2026E | 旧2027E | 新2026E | 新2027E | 幅度2026E | 幅度2027E`
    幅度应由 (新 ÷ 旧 − 1) × 100 得到。末 k 个百分号即 k 个幅度，其前 2k 个数值
    恰好是「旧 k 个」与「新 k 个」。符号相反而幅度相当 → 方向写错；幅度不符 → 数值错。
    """
    toks = _tokens(str(claim.get("sentence") or ""))
    pcts = [t for t in toks if t[1]]
    plains = [t for t in toks if not t[1]]
    k = len(pcts)
    if k < 1 or len(plains) < 2 * k:
        return None
    if not all(t[1] for t in toks[-k:]):              # 幅度列必须在行尾（成块）
        return None
    prev = [v for v, _ in plains[-2 * k:-k]]
    post = [v for v, _ in plains[-k:]]
    exp = [v for v, _ in pcts[-k:]]
    if any(v <= 0 for v in prev + post):              # 负值/零值指标不适用本式
        return None
    if any(not (0 < abs(e) <= 50) for e in exp):      # 幅度列应是百分比量级
        return None
    sign_bad, value_bad = [], []
    for a, b, e in zip(prev, post, exp):
        calc = (b / a - 1) * 100
        if abs(calc) > Decimal("50"):                 # 真实幅度过大 → 不是这一形态
            return None
        if (calc > 0) != (e > 0) and abs(abs(calc) - abs(e)) <= CALC_TOL:
            sign_bad.append((e, calc))
        elif abs(calc - e) > CALC_TOL:
            value_bad.append((e, calc))
    if sign_bad:
        e, calc = sign_bad[0]
        return ("SIGN_ERROR",
                f"表内自校验：调整幅度列写「{e}%」，而用同一行的数据列按"
                f"「(新值 ÷ 旧值 − 1)」重算得 {calc:.2f}%——两者数值相当、符号相反，"
                f"正负号写反了。",
                f"{calc:.2f}%")
    if value_bad:
        e, calc = value_bad[0]
        return ("VALUE_ERROR",
                f"表内自校验：调整幅度列写「{e}%」，而用同一行的数据列按"
                f"「(新值 ÷ 旧值 − 1)」重算得 {calc.quantize(Decimal('0.1'))}%——"
                f"幅度列与数据列不自洽。",
                f"{calc.quantize(Decimal('0.1'))}%")
    return None


def _doc_unit(claims: list[dict]) -> str | None:
    """文档统一列报单位：报表类表行标签括号内出现最多的量级单位。"""
    tally: dict[str, int] = {}
    for c in claims:
        if c.get("kind") not in ("TABLE_LABEL", "TABLE_ROW", "TABLE_HEADER"):
            continue
        m = PAREN_UNIT_RE.search(str(c.get("sentence") or "").split("|")[0])
        if m and m.group(1) in MAGNITUDE_UNITS:
            tally[m.group(1)] = tally.get(m.group(1), 0) + 1
    if not tally:
        return None
    return max(tally.items(), key=lambda kv: kv[1])[0]


def _text_unit(claims: list[dict], fallback: str | None = None) -> str:
    """正文口径的量级单位：研报**正文**习惯写「亿元」，表内才用「百万元」。

    单说"千万元不是认可的单位"没用——复核的人要知道该改成哪一种。本办法统计正文里
    出现最多的合法量级单位，作为"应改写成……"的依据；正文没有任何金额时退回表内口径。
    """
    tally: dict[str, int] = {}
    for c in claims:
        if c.get("kind") != "TEXT":
            continue
        for m in NUM_RE.finditer(str(c.get("sentence") or "")):
            unit = m.group(3)
            if unit not in UNIT_SCALE:
                continue
            tally[_unit_alias(unit)] = tally.get(_unit_alias(unit), 0) + 1
    if tally:
        return max(tally.items(), key=lambda kv: kv[1])[0]
    return _unit_alias(fallback) if fallback else "亿元"


def _unit_alias(unit: str | None) -> str:
    """同一量级的单/双字写法归一到常用写法：「亿」→「亿元」、「万」→「万元」。"""
    return {"亿": "亿元", "万": "万元", "千": "千元", "万亿": "万亿元"}.get(unit or "", unit or "")


def _num_str(v) -> str:
    """数值 → 不带千分位、不带科学计数法的显示串。"""
    try:
        d = Decimal(str(v)).normalize()
    except (InvalidOperation, TypeError, ValueError):
        return "—"
    return f"{d:f}"


def _amount_in_unit(value, unit: str) -> str:
    """金额（以"元"为单位）换算到指定量级单位后显示，保留两位小数、去掉多余的 0。"""
    scale = UNIT_SCALE.get(_unit_alias(unit))
    if not scale:
        return f"{_num_str(value)}元"
    try:
        q = (Decimal(str(value)) / scale).quantize(Decimal("0.01")).normalize()
    except (InvalidOperation, TypeError, ValueError):
        return f"{_num_str(value)}元"
    return f"{_num_str(q)}{_unit_alias(unit)}"


def _unit_consistency(claims: list[dict]) -> list[dict]:
    """RC04：① 表行标签单位与文档统一口径不一致；② 正文数值用了非量级单位写法。"""
    out, seen = [], set()
    doc_unit = _doc_unit(claims)
    text_unit = _text_unit(claims, doc_unit)
    for c in claims:
        cid = c.get("claim_id")
        s = str(c.get("sentence") or "")
        if c.get("kind") in ("TABLE_LABEL", "TABLE_ROW", "TABLE_HEADER"):
            label = s.split("|")[0].strip()
            m = PAREN_UNIT_RE.search(label)
            if (not m or doc_unit is None):
                continue
            unit = m.group(1)
            if unit not in MAGNITUDE_UNITS or unit == doc_unit:
                continue
            prefix = label[:m.start()].strip()
            if not any(prefix.startswith(p) for p in STATEMENT_PREFIXES):
                continue                          # 每股收益（元）等非报表口径行
            out.append(_claim_record(
                UNIT_RULE_ID, c, "UNIT_SCALE",
                f"单位口径不一致：本行标签把「{prefix}」的单位标成「{unit}」，"
                f"而本研报表内其余报表行统一用「{doc_unit}」——同一份研报里混用了两种"
                f"列报单位。应写作「{prefix}（{doc_unit}）」，或把全表单位统一成一种。",
                {"raw": label, "value": ""}, {"raw": doc_unit, "value": ""}, "—",
                expected=f"{prefix}（{doc_unit}）"))
            seen.add(cid)
        else:
            # 正文里的畸形/非本口径单位（如「862.3 亿万元」「1721 千万元」）。
            # ★ 必须遍历句中**全部**数值再判：同句常先出现一个合法单位（1741.4 亿元），
            #   只看第一个匹配（.search）会把紧随其后的畸形单位整条漏掉。
            bad = next((m for m in NUM_RE.finditer(s) if m.group(3) not in UNIT_SCALE), None)
            if bad is None or cid in seen:
                continue
            num, bad_unit = bad.group(2), bad.group(3)
            out.append(_claim_record(
                UNIT_RULE_ID, c, "UNIT_SCALE",
                f"单位写法有误：研报把金额写成「{num}{bad_unit}」，而「{bad_unit}」不是常用的"
                f"量级单位写法（常用：万元 / 百万元 / 亿元 / 万亿元）。本研报其余金额统一按"
                f"「{text_unit}」表述，这一处应写作「{num}{text_unit}」——数字不动、只写错"
                f"单位，会让人把金额读错量级。",
                {"raw": f"{num}{bad_unit}", "value": ""},
                {"raw": text_unit, "value": ""}, "—",
                expected=f"{num}{text_unit}"))
            seen.add(cid)
    return out


def _intra_report_checks(claims: list[dict]) -> tuple[list[dict], set[str]]:
    """RC05：研报自身的一致性（预测区间 / 同比方向 / 正文与表行数值）。"""
    out: list[dict] = []
    flagged: set[str] = set()
    texts = [c for c in claims if c.get("kind") == "TEXT"]
    rows = [c for c in claims if c.get("kind") == "TABLE_ROW"]
    all_pct_rows = [(c, _pct_row_values(c)) for c in rows]
    all_pct_rows = [(c, v) for c, v in all_pct_rows if v]

    def add(claim, cause, reason, left, right, diff, expected="—"):
        out.append(_claim_record(INTRA_RULE_ID, claim, cause, reason, left, right, diff,
                                 expected))
        flagged.add(claim.get("claim_id"))

    for c in texts:
        cid = c.get("claim_id")
        s = str(c.get("sentence") or "")
        year = _report_year(cid)

        # (a) 预测年份区间必须自报告年度**之后**起算。
        #   研报的"预计/预测"指向尚未结束的年度，区间起点不应 ≤ 报告年度
        #   （2026 中报写"预计 2025-2027 年"即含已披露的 2025，属期间张冠李戴）。
        #   ★ 不能拿"区间年份集合 ≠ 表头 E 年份集合"来判：表头常列 4 个预测年
        #   （2025E~2028E）而正文只前瞻 3 年，集合天然不等，会产出大量误报。
        m = RANGE_RE.search(s)
        if m and year is not None and FORECAST_MARKER_RE.search(s):
            y1, y2 = int(m.group(1)), int(m.group(2))
            if y1 <= year and y2 - y1 <= 6:
                add(c, "PERIOD_MISMATCH",
                    f"预测区间写的是 {y1}-{y2} 年，起点 {y1} 年不晚于本研报所属年度 {year} 年；"
                    f"向前的预测只能从尚未结束的年度起算，{y1} 年已披露、不能当预测起点。",
                    {"raw": f"{y1}-{y2}年", "value": ""},
                    {"raw": f"起点应 > {year}", "value": ""}, "—",
                    f"起点应晚于 {year} 年（如 {year + 1} 年起）")

        # (b) 「20XX年…目标/指引/展望」指向了已经结束的年度
        for mm in list(TARGET_AFTER_RE.finditer(s)) + list(TARGET_BEFORE_RE.finditer(s)):
            yr = int(mm.group(1) or mm.group(2))
            if year is not None and yr <= year:
                add(c, "PERIOD_MISMATCH",
                    f"经营目标/展望写的是 {yr} 年，未晚于本研报所属年度 {year} 年；"
                    f"目标类表述指向的是尚未结束的年度，{yr} 年已披露、不能作为目标年度。",
                    {"raw": f"{yr}年", "value": ""},
                    {"raw": f"应晚于 {year}", "value": ""}, "—",
                    f"应晚于 {year} 年（如 {year + 1} 年）")
                break

        # (c) 「截至 20XX年X月末」晚于本研报所属年度
        m = CUTOFF_RE.search(s)
        if m and year is not None and int(m.group(1)) > year:
            add(c, "PERIOD_MISMATCH",
                f"时点写的是「{m.group(0)}」，晚于本研报所属年度 {year} 年；"
                f"「截至……」的时点不可能晚于报告年度，所引金额实际属于 {year} 年末。",
                {"raw": m.group(0), "value": ""},
                {"raw": f"≤ {year}年", "value": ""}, "—",
                f"{year}年末")

    # (d) 正文与全百分号表行的一致性：≥2 个精确相同且 ≥1 个不同 → 表行数值有误
    for c in texts:
        s = str(c.get("sentence") or "")
        series = [Decimal(x.replace(",", "")) for x in SERIES_PCT_RE.findall(s)]
        if len(series) < 2:
            continue
        k = len(series)
        for row, vals in all_pct_rows:
            if len(vals) < k:
                continue
            tail = vals[-k:]
            same = sum(1 for a, b in zip(series, tail) if a == b)
            if same >= 2 and same < k:
                diff_i = next(i for i, (a, b) in enumerate(zip(series, tail)) if a != b)
                add(row, "VALUE_ERROR",
                    f"正文与表行数值不一致：正文写 {_fmt(series)}，本表行对应列写 "
                    f"{_fmt(tail)}（{k} 个里只有 {same} 个完全相同）；同一组数值出现两个"
                    f"版本，需以正文/表格的同一口径逐列核对。",
                    {"raw": _fmt(tail), "value": str(tail[diff_i])},
                    {"raw": _fmt(series), "value": str(series[diff_i])},
                    str(tail[diff_i] - series[diff_i]), _num_str(series[diff_i]))

    # (e) 正文「同比减少 X%/Y%/Z%」与全百分号表行方向相反、幅度相同 → 方向写错
    for c in texts:
        s = str(c.get("sentence") or "")
        for mm in SIGN_SERIES_RE.finditer(s):
            down = mm.group(1) in YOY_DOWN_WORDS
            nums = [Decimal(x.replace(",", ""))
                    for x in re.findall(r"\d[\d,]*(?:\.\d+)?", mm.group(2))]
            if len(nums) < 2:
                continue
            k = len(nums)
            for row, vals in all_pct_rows:
                if len(vals) < k:
                    continue
                tail = vals[-k:]
                # 方向一致才不是本类问题：正文说"减少"(down=True) 应对应全负值
                # （all(v>0)=False），说"增长"(down=False) 应对应全正值。
                if down != all(v > 0 for v in tail):
                    continue
                if all(a == b for a, b in zip(nums, tail)):
                    add(c, "SIGN_ERROR",
                        f"同比方向与预报表行相反：正文写「同比{mm.group(1)}"
                        f"{_fmt(nums)}」，而表行同列数值为 "
                        f"{_fmt(tail)}（清一色{'正' if all(v > 0 for v in tail) else '负'}值），"
                        f"正文与表格的方向对不上。",
                        {"raw": f"同比{mm.group(1)}{_fmt(nums)}", "value": ""},
                        {"raw": _fmt(tail), "value": ""}, "—",
                        f"同比{'增长' if all(v > 0 for v in tail) else '减少'}{_fmt(nums)}")
                    break
    # 同一 claim 可能同时命中多支规则，去重后只留第一条
    dedup, seen = [], set()
    for r in out:
        key = (r["claim_id"], r["cause"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(r)
    return dedup, flagged


def _fmt(vals) -> str:
    """数值序列 → 逗号分隔的显示串（**返回字符串**：报告表格单元格必须是文本，
    返回 list 会被 reportlab 当成"多个 flowable"而在渲染时报 wrapOn 错误）。"""
    out = []
    for v in vals:
        d = Decimal(v)
        out.append(f"{d.normalize():f}" if d == d.to_integral_value() else f"{d:f}")
    return ", ".join(out)


def _run_detectors(claims: list[dict]) -> tuple[list[dict], set[str]]:
    """跑全部研报内检测器，返回 (结果条目, 已判定的 claim_id 集合)。"""
    results: list[dict] = []
    flagged: set[str] = set()
    for c in claims:                                    # RC03 表内自校验
        if c.get("kind") != "TABLE_ROW":
            continue
        hit = _selfcheck_row(c)
        if not hit:
            continue
        cause, reason, expected = hit
        results.append(_claim_record(
            CALC_RULE_ID, c, cause, reason,
            {"raw": str(c.get("sentence") or ""), "value": ""},
            {"raw": "按(新值 ÷ 旧值 − 1)重算", "value": ""}, "—",
            expected=expected))
        flagged.add(c.get("claim_id"))
    results.extend(_unit_consistency(claims))           # RC04 单位口径
    flagged |= {r["claim_id"] for r in results if r["rule_id"] == UNIT_RULE_ID}
    intra, intra_ids = _intra_report_checks(claims)     # RC05 研报内一致性
    results.extend(intra)
    flagged |= intra_ids
    return results, flagged


def check(matches: list[dict], claims_by_id: dict, records: list[dict]) -> dict:
    """对每条已匹配的 claim 做数值比对，返回 interfaces.md §4.1 形态的结果。"""
    fields = _load_fields()
    zh_map = _display_names(fields)
    tol_cfg = _load_tolerance()
    profile = tol_cfg["profiles"][TOLERANCE_PROFILE]
    idx = _index_records(records)
    # 「应为（正确值）」列里金额用哪种量级单位表述：跟该研报**正文**的口径走
    # （正文习惯写「亿元」，表内才用「百万元」）。正文没有任何金额时退回表内口径。
    _all_claims = list(claims_by_id.values())
    report_unit = _text_unit(_all_claims, _doc_unit(_all_claims))
    # 同比基准索引（D2/RC02 用）：records 在整个 check 调用中固定不变，只构建一次。
    # ★ 不要放进下面的 matches 循环里逐条重建——那是 O(条数 × 记录数) 的无谓开销。
    fy = _fy_index(records)

    # 先跑"研报内一致性"检测器（RC03/RC04/RC05）：它们只读研报自身，不依赖财报字段
    # 与模型输出，故离线也必然可执行。
    # ★ 此前这里把"已被检测器判定的 claim"整条从 RC01/RC02 流程里排除，看似能避免
    #   "同一处植错记成两条"，实则是**漏报的正负号根因**：一句话里可以同时有两类错误
    #   （实测「营业总收入1721 千万元/同比+1.2%」既是单位错、又是同比方向错），按 claim
    #   整体排除会把第二类错误整条丢掉——标准答案里"同比 +1.2% 方向写反"就这样漏了。
    #   改为**按(claim_id, cause)去重**（见函数末尾）：同类错误只留检测器那条（带页码/
    #   表行位置），不同类错误各自保留。
    det_results, _det_fail_ids = _run_detectors(list(claims_by_id.values()))

    results, skipped = list(det_results), []
    for m in matches:
        cid, code, period = m.get("claim_id"), m.get("item_code"), m.get("period")
        base = {"rule_id": RULE_ID, "dimension": "C研", "claim_id": cid,
                "item_code": code, "period": period,
                "evidence": m.get("evidence", {})}

        # 缺科目/期间 → 无法比对，转人工复核（不猜）
        if not code or not period:
            skipped.append({**base, "status": "REVIEW",
                            "reason": "未匹配到科目或期间，无法比对"})
            continue

        recs = idx.get((code, period))
        if not recs:
            skipped.append({**base, "status": "REVIEW",
                            "reason": f"结构化数据中无 {code}@{period}，需数据侧确认"})
            continue

        claim = claims_by_id.get(cid, {})
        sentence = claim.get("sentence", "")
        rec = recs[0]

        # 预测/多列表格行：列头年份在抽取时丢失，行内数值与年份列无法对齐
        # （见 is_forecast_row）。此时硬判必然张冠李戴，转人工复核。
        # ★ 预测类指标（含"调整幅度"）本质上需要"上一版预测/一致预期"作为外部基线，
        #   本系统只用年报数据、不引外部预期（derived.py 铁律 1），故无法确定性核验，
        #   只能如实转人工复核，不猜、不补。
        if is_forecast_row(sentence):
            skipped.append({**base, "status": "REVIEW",
                            "reason": "研报为预测/多列表格行：列头年份已丢失，且预测类指标"
                                      "（含调整幅度）需上一版预测基线，无法确定性核验，"
                                      "需人工复核"})
            continue

        # 期间不许混（interfaces.md §1.5）：研报写的是单季/半年度数，财报记录是全年累计，
        # 两者不可比。直接判不可比而不是判错——否则每句"Q4实现…"都会变成一条误报。
        if QUARTER_RE.search(sentence) and rec.get("period_type") == "FY":
            skipped.append({**base, "status": "REVIEW",
                            "reason": "研报为单季/半年度口径，与年度累计不可比"})
            continue

        # 单条解析异常不得打断整体流程（与 llm 侧"失败转人工复核"一致）：
        # 某句研报措辞触发解析边界时，只丢这一条，其余照常比对。
        try:
            # 比率类（毛利率/净利率/资产负债率）用百分号解析，其余科目用金额解析
            figs = (associate_percent(sentence, zh_map) if code in RATIO_CODES
                    else associate_figures(sentence, zh_map))
        except Exception as e:                                  # noqa: BLE001
            skipped.append({**base, "status": "REVIEW",
                            "reason": f"句内金额解析异常（{type(e).__name__}: {e}），需人工复核"})
            continue
        fig = figs.get(code)
        if not fig:
            skipped.append({**base, "status": "REVIEW",
                            "reason": "句中该指标名下未定位到金额（或该指标非本句主语），需人工复核"})
            continue

        # 多值枚举（如「预计归母净利润847/904/982 亿元」）：枚举里哪一个属于本期间无法确定。
        # NUM_RE 只截得到末位「982 亿元」（实为 2028E），拿它去比 2025 年财报必然张冠李戴，
        # 凭空产出一条误报。转人工复核，不猜。
        if fig.get("enumerated"):
            skipped.append({**base, "status": "REVIEW",
                            "reason": f"研报为多值枚举（{fig['raw']}），无法判定哪一个是"
                                      f"本期间（{period}）的数值，需人工复核"})
            continue

        truth = Decimal(recs[0]["value"])
        diff = fig["value"] - truth
        tol = tolerance_for(profile, fig["value"], truth)
        value_ok = abs(diff) <= tol

        # D1 期间短语一致性：研报把期间写错、金额却取自正确的期，数值比对永远抓不到
        # （金额本来就没错）——「截至 2026年6月末合同负债80.1亿元」即是此类。期间修饰
        # 取指标名**之前**最近的"20XX年"（见 period_year_before）。
        # 分级：金额对得上 → 判定期间写错，FAIL；金额也不符 → 可能是模型的期间匹配本身
        # 有误，不作断言，转人工复核，避免把系统自身的匹配错误记成研报的错。
        period_year = period_year_before(sentence, fig)

        if fig["bad_unit"]:
            # 单位写法本身非法（如"亿万元"）。数值即便凑巧接近也不放行——
            # 这正是"单位错误"这一类要抓的东西，不能因为数值误差在容差内就判通过。
            ok, cause = False, "UNIT_SCALE"
            # 「应为」＝数字不动、把单位换成该研报正文常用的量级单位写法
            m_num = re.match(r"-?\d[\d,]*(?:\.\d+)?", str(fig.get("num_raw") or ""))
            expected = f"{m_num.group(0)}{report_unit}" if m_num else "—"
            reason = (f"单位写法有误：研报把金额写成「{fig['num_raw']}」，而"
                      f"「{fig['bad_unit']}」不是常用的量级单位写法（常用：万元 / 百万元 / "
                      f"亿元）。数字本身没错，只把单位改成「{report_unit}」，即应为"
                      f"「{expected}」。")
        elif period_year is not None and not str(period).startswith(period_year):
            if value_ok:
                ok, cause = False, "PERIOD_MISMATCH"
                expected = f"{period_year}年（与财报取值期间 {period} 一致）"
                reason = (f"期间写错：研报表述为 {period_year} 年，而金额取自财报的 "
                          f"{period} 期间（金额本身一致）——期写错、数却对，属期间张冠李戴。"
                          f"应写作 {period_year} 年。")
            else:
                skipped.append({**base, "status": "REVIEW",
                                "reason": f"研报表述期间为 {period_year} 年，与匹配期间 "
                                          f"{period} 不一致且金额亦不符，疑为期间匹配错误，"
                                          f"需人工复核"})
                continue
        else:
            ok = value_ok
            expected = (_amount_in_unit(truth, report_unit) if code not in RATIO_CODES
                        else f"{_num_str(truth)}%")
            cause, reason = ("", "") if ok else classify_diff(fig["value"], truth)
            if not ok:
                reason = f"{reason}。财报口径该期间的正确值为 {expected}"
                # 口径互认：换另一净利率口径复核一次（见 MARGIN_ALT）。研报口语的
                # "净利率"常写归母口径，与主口径（净利润÷营业总收入）本就不同值——
                # 差异属列报口径不同，不是引用错误，判 PASS 并注明依据。
                alt = margin_alt_truth(code, period, idx)
                if alt is not None and abs(fig["value"] - alt) <= tolerance_for(
                        profile, fig["value"], alt):
                    ok, cause = True, ""
                    truth = alt
                    diff = fig["value"] - truth
                    tol = tolerance_for(profile, fig["value"], truth)
                    expected = (_amount_in_unit(truth, report_unit)
                                if code not in RATIO_CODES else f"{_num_str(truth)}%")
                    reason = (f"口径差异：研报表述与「{MARGIN_ZH[MARGIN_ALT[code]]}」"
                              f"一致（{truth}），非引用错误")

        results.append({
            **base,
            "status": "PASS" if ok else "FAIL",
            "left": {"source": "research_report", "raw": fig["num_raw"],
                     "value": str(fig["value"])},
            "right": {"item_code": code, "period": period, "value": str(truth)},
            "diff": str(diff),
            "tolerance": str(tol),
            "severity": "MED" if ok else "HIGH",
            "cause": cause,
            "reason": reason,
            "expected": expected,
            "matched_text": fig["raw"],
            "truth_page": recs[0].get("page"),
            "truth_chunk_id": recs[0].get("chunk_id"),
        })

        # D2 同比核验（独立规则 RC02）：数值之后紧跟的"同比±X%"是否与财报口径同比一致。
        # 只用于**金额类、纯年度期间**：比率类研报惯例用"个百分点"表述，基数与百分比不同，
        # 不在此规则判定；单季/半年度句（QUARTER_RE）的同比基期是同期而非上一年，同样不判。
        if (rec.get("period_type") == "FY" and code not in RATIO_CODES
                and not QUARTER_RE.search(sentence)):
            ya = yoy_after(sentence, fig)
            if ya is not None:
                yoy_val, yoy_raw, yoy_enumerated = ya
                yoy_base = {**base, "rule_id": YOY_RULE_ID,
                            "left": {"source": "research_report", "raw": yoy_raw,
                                     "value": str(yoy_val)},
                            "matched_text": yoy_raw}
                truth_yoy = yoy_truth(code, period, fy)
                if truth_yoy is None:
                    # 财报侧缺上一期（如只上传了单份年报）→ 同比既无真值可比，也必须**显式**
                    # 留痕。此前这里是静默跳过：研报把同比方向写反，报告上完全看不见——
                    # 那正是"正负号漏报"的结构性温床。
                    skipped.append({**yoy_base, "status": "REVIEW",
                                    "reason": f"研报标注了同比（{yoy_raw}），但财报侧缺少"
                                              f"上一期（上一年度）数据，方向与幅度无法核验，"
                                              f"需人工复核"})
                elif yoy_enumerated:
                    skipped.append({**yoy_base, "status": "REVIEW",
                                    "reason": f"研报同比为多值枚举（{yoy_raw}），无法判定"
                                              f"本期间（{period}）口径，需人工复核"})
                else:
                    yoy_tol = yoy_tolerance(truth_yoy)
                    if abs(yoy_val - truth_yoy) > yoy_tol:
                        yoy_diff = yoy_val - truth_yoy
                        yoy_base = {**yoy_base,
                                    "right": {"item_code": code, "period": period,
                                              "value": str(truth_yoy)},
                                    "diff": str(yoy_diff), "tolerance": str(yoy_tol)}
                        # 符号相反而幅度相当 → 方向写错（如真值 -1.2% 写成 +1.2%），可断言；
                        # 仅幅度不符 → 可能是口径/四舍五入，转人工复核，不作断言。
                        if ((yoy_val > 0) != (truth_yoy > 0)
                                and abs(abs(yoy_val) - abs(truth_yoy)) <= yoy_tol):
                            results.append({**yoy_base, "status": "FAIL", "severity": "HIGH",
                                            "cause": "SIGN_ERROR",
                                            "reason": f"同比方向写反：研报写「{yoy_raw}」，"
                                                      f"而按财报数据算得的同比为 "
                                                      f"{truth_yoy}%——幅度对得上，正负号相反，"
                                                      f"应写作 {truth_yoy}%。",
                                            "expected": f"{_num_str(truth_yoy)}%"})
                        else:
                            skipped.append({**yoy_base, "status": "REVIEW",
                                            "reason": f"研报同比 {yoy_raw} 与财报口径同比 "
                                                      f"{truth_yoy}% 不符，需人工复核",
                                            "expected": f"{_num_str(truth_yoy)}%"})

    # ── 收尾去重（与函数开头那段说明配套）────────────────────────────────────────
    # ① 检测器（RC03/RC04/RC05）与 RC01/RC02 对**同一 claim、同一 cause** 只留一条：
    #    同一处植错被两条规则同时抓到属正常，但只应计一次；检测器条目带页码/表行位置，
    #    可复核性更强，故以它为准（**不同 cause** 的结论一律保留，见开头的漏报说明）。
    decided = {(r.get("claim_id"), r.get("cause")) for r in det_results}
    results = [r for r in results
               if not (str(r.get("rule_id") or "").startswith(("RC01", "RC02"))
                       and (r.get("claim_id"), r.get("cause")) in decided)]
    # ② 已判为异常（FAIL）的 claim 不再重复进「待人工复核」清单：同一处问题既进 FAIL
    #    又进 REVIEW，会让异常计数翻倍、复核清单出现伪条目（如"该 claim 无结构化数据"
    #    这类伪条目，在该 claim 已判出真异常时毫无信息增量）。
    failed = {r.get("claim_id") for r in results if r.get("status") == "FAIL"}
    return {"results": results,
            "review": [r for r in skipped if r.get("claim_id") not in failed]}


def dedup_claim_matches(matches: list[dict]) -> tuple[list[dict], list[dict]]:
    """同一指标在同一句里被枚举多个期间时**去多义化**。返回 (保留, 转人工复核)。

    一句研报引用通常只对应一个期间。模型偶尔会对同一指标枚举多个期间（实测：一句
    「资产负债率 | 22.0% | 19.5% | …」被展开成 2023/2024/2025 三个期间，同一数值
    被拿去比三年的财报，凭空产出 3 条互相矛盾的「异常」）。规则：

      - 只保留 period 中的年份在句子里**显式出现**的那几条；
      - 句中没有任何年份可对应 → 说明列头年份在抽取时已丢失，整组转人工复核，
        不用"行内第一个数值"去强行比对；
      - 不同指标（同句多指标各一条）不受影响，只有同名指标的重复期间才去重。
    """
    sent_by_id: dict[str, str] = {}
    for m in matches:
        sent_by_id.setdefault(m.get("claim_id"),
                              (m.get("evidence") or {}).get("sentence") or "")
    by_claim: dict[str, list[dict]] = {}
    for m in matches:
        by_claim.setdefault(m.get("claim_id"), []).append(m)

    kept: list[dict] = []
    demoted: list[dict] = []
    for cid, ms in by_claim.items():
        sentence = sent_by_id.get(cid) or ""
        years = set(YEAR_TOKEN_RE.findall(sentence))
        by_code: dict[str, list[dict]] = {}
        for m in ms:
            by_code.setdefault(m.get("item_code") or "", []).append(m)
        for code, group in by_code.items():
            if len(group) == 1:
                kept.extend(group)
                continue
            hit = [m for m in group
                   if set(YEAR_TOKEN_RE.findall(str(m.get("period") or ""))) & years]
            if hit:
                kept.extend(hit)
            else:
                demoted.append({
                    "claim_id": cid, "item_code": code, "period": None,
                    "reason": "同句命中多个期间且句中无对应年份，期间无法对齐，转人工复核",
                    "sentence": sentence,
                })
    return kept, demoted


def run_research_check(claims_path: str, matches: list[dict],
                       records: list[dict], run_id: str,
                       unmatched: list[dict] | None = None) -> dict:
    """供 pipeline 调用的入口：读研报原文、比对、落盘。

    ``unmatched``：模型压根没作答的 claim（典型是离线缓存未命中）。必须一并记成
    「待人工复核」——否则这批引用在报告与网页里凭空消失，读者会以为它们已被核查。
    """
    claims = json.loads((ROOT / claims_path).read_text(encoding="utf-8"))
    by_id = {c["claim_id"]: c for c in claims}
    out = check(matches, by_id, records)
    # 已被研报内检测器（RC03/RC04/RC05）判定为异常的 claim，不再重复记为「待复核」：
    # 同一处植错若既进 FAIL 又进 REVIEW，异常计数会翻倍、复核清单也会出现伪条目。
    decided = {r.get("claim_id") for r in out["results"]}
    for u in unmatched or []:
        cid = u.get("claim_id")
        if cid in decided:
            continue
        c = by_id.get(cid) or {}
        out["review"].append({
            "rule_id": RULE_ID, "dimension": "C研", "claim_id": cid,
            "item_code": u.get("item_code"), "period": u.get("period"),
            "status": "REVIEW",
            "reason": u.get("reason") or "模型未返回该条的匹配结果",
            # 位置：页码 + 章节 + 段号 / 表行号 + 表标题（与 RC01 证据口径一致）
            "evidence": {"file": c.get("source"), "section": c.get("section"),
                         "sentence": u.get("sentence") or c.get("sentence"),
                         "page": c.get("page"), "para_no": c.get("para_no"),
                         "row_no": c.get("row_no"), "caption": c.get("caption"),
                         "verified": bool(c)},
        })
    p = ROOT / "runs" / run_id / "rules" / "research_claims.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True),
                 encoding="utf-8")
    return out
