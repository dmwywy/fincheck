# src/viz.py —— 可视化呈现层：CSI 五维雷达图 / 异常分布堆叠柱状图 / 研报纠错对比
"""三张核查可视化的图形数据组装与渲染。**只做呈现，不做数值计算**。

口径纪律（与三种报告、网页端共用同一份数据，禁止各算各的）：
  - 「异常 / 待复核」只有一处口径 —— ``src.report.merged_anomaly_counts``。本模块按维度
    拆分时沿用同一份输入（``rules/results.json`` 的 results + ``rules/research_claims.json``
    的 results 与 review 两段），拆分后的合计必须与 ``merged_anomaly_counts`` 相等；
  - CSI 维度得分直接取 ``rules/csi.json`` 的 ``dimensions[*].score``，不重算、不改写；
  - 「应为（正确值）」取 ``src.report.expected_text``，无确定性结论时是「—」，不臆造；
  - 「不适用」维度的呈现口径与 ``src.report._csi_exec_note`` / ``_csi_caveat`` 一致：
    图上标为「不适用」，并注明权重已按比例分摊至其余维度。

两种渲染目标共用同一份图形数据：
  - 网页端 / HTML 报告：Plotly 交互图（``csi_radar`` / ``anomaly_distribution`` /
    ``research_error_compare``，plotly.js 由 ``figures_html`` 只内联一次，报告可离线打开）；
  - PDF 报告：reportlab.graphics 手绘静态图（``csi_radar_drawing`` /
    ``anomaly_distribution_drawing``），零新增依赖。

★ plotly 不在模块顶层 import：缺少该可选依赖时本模块仍可被导入，报告生成会跳过图形而
  不报错（报告内容不得因图表不可用而缺失）。
"""
from __future__ import annotations

import math

from src.report import (DIM_ZH, expected_text, loc_str, merged_anomaly_counts,
                        review_entries)

# 维度顺序固定：与 csi.json / report.py 的维度命名一致（C内/C间/C期/C附/C研）
DIM_ORDER = ("C内", "C间", "C期", "C附", "C研")

# ── 单一配色源 ───────────────────────────────────────────────────────────────
# ★ 图表与「研报常见错误类型科普」卡片必须**同色**：读者在「研报纠错对比图」的
#   「错误类型」列看到什么颜色，科普卡片上就是同一类错误。所有色值集中在此，
#   改配色只改这一处（其余模块一律引用 PALETTE / CAUSE_COLOR，不得再写字面量）。
PALETTE = {
    "accent": "#2D6CDF",   # 主色：表头、雷达
    "fail": "#C0392B",     # 异常（确定性结论）
    "review": "#E0A33C",   # 待人工复核
    "unit": "#C77C1A",     # 单位量级错误
    "sign": "#7B4FA0",     # 正负号错误
    "ink": "#333333",      # 正文文字
    "muted": "#666666",    # 次要文字
    "axis": "#9AA3AE",     # 坐标轴
    "grid": "#D8DCE2",     # 网格线
    "soft": "#CFE0F7",     # 雷达多边形填充
    "other": "#5B6B7C",    # 未归类的错误类型
}

# 研报引用错误的归因枚举（定义见 src/research_check.py）与其主题色。
# 四类错误各用一个色相：红（数值）/ 琥珀（单位量级）/ 蓝（期间口径）/ 紫（正负号）。
CAUSE_ZH = {"VALUE_ERROR": "数值错误", "UNIT_SCALE": "单位量级错误",
            "PERIOD_MISMATCH": "期间不符", "SIGN_ERROR": "正负号错误"}
CAUSE_COLOR = {"VALUE_ERROR": PALETTE["fail"], "UNIT_SCALE": PALETTE["unit"],
               "PERIOD_MISMATCH": PALETTE["accent"], "SIGN_ERROR": PALETTE["sign"]}
CAUSE_OTHER_COLOR = PALETTE["other"]

FAIL_COLOR = PALETTE["fail"]
REVIEW_COLOR = PALETTE["review"]
NA_NOTE = "不适用维度的权重已按比例分摊至其余维度"
PDF_FONT = "STSong-Light"

# 「研报常见错误类型科普」四张卡片：(归因, 标题, 例子原文, 一句话怎么看)
# ★ 文案与网页端 expander、HTML 报告共用同一份，禁止两处各写一套。
ERROR_TYPE_CARDS = (
    ("SIGN_ERROR", "正负号错误", "研报写「同比 +1.2%」，实为 -1.2%",
     "先看数字前有没有负号被吞掉：方向词（增长 / 减少）与正负号必须一致。"),
    ("UNIT_SCALE", "单位量级错误", "「1721 千万元」≠「1721 亿元」",
     "看单位：千万元与亿元差 10 倍，量级单位写错会让金额整体偏移。"),
    ("PERIOD_MISMATCH", "期间错误", "「2026 年 6 月末」≠「2025 年末」",
     "对不上年度就存疑：期间写错时金额常常是对的，纯数值比对抓不到。"),
    ("VALUE_ERROR", "数值错误", "研报写 232 亿元，财报实为 222.75 亿元",
     "和年报原文逐位比对：这是最直接的一类，逐位核对即可发现。"),
)


# ---------------- 数据组装（不含任何计算） ----------------

def _ordered_dims(keys) -> list[str]:
    """按固定维度顺序排列，未知维度追加在后（避免顺序漂移导致图形对不上）。"""
    ks = list(keys)
    return [k for k in DIM_ORDER if k in ks] + [k for k in ks if k not in DIM_ORDER]


def anomaly_by_dimension(rule_results: list[dict], research: dict) -> dict[str, dict]:
    """把「异常 / 待复核」按维度拆分，口径与 ``merged_anomaly_counts`` 完全一致。

    统计对象 = 规则级 results 段 + 研报级 results 段（FAIL / REVIEW）+ 研报级 review 段
    （整段都是「程序无法自动判定」的待复核条目）。拆分后的合计必须等于
    ``merged_anomaly_counts`` 的返回值，否则说明出现了第二个计数口径。
    """
    out: dict[str, dict] = {}

    def _bump(dim, key):
        d = out.setdefault(dim or "?", {"fail": 0, "review": 0})
        d[key] += 1

    research = research or {}
    for r in list(rule_results or []) + list(research.get("results") or []):
        status = (r or {}).get("status")
        if status == "FAIL":
            _bump(r.get("dimension"), "fail")
        elif status == "REVIEW":
            _bump(r.get("dimension"), "review")
    # review 段：整段视为待复核，维度缺省按研报维度 C研 计。
    # ★ 走 review_entries 与报告/网页共用同一过滤（排除已单列的预测值、口径不可比两桶），
    #   否则图上合计会与 merged_anomaly_counts 对不上——那就是第二个计数口径。
    for r in review_entries(research):
        _bump((r or {}).get("dimension") or "C研", "review")
    return out


def dimension_rows(rule_results: list[dict], research: dict) -> list[dict]:
    """按维度给出「异常 / 待复核 / 合计」行，供图表与测试共用。"""
    by = anomaly_by_dimension(rule_results, research)
    rows = []
    for k in _ordered_dims(by):
        d = by[k]
        rows.append({"dimension": k, "label": DIM_ZH.get(k, k),
                     "fail": d["fail"], "review": d["review"],
                     "total": d["fail"] + d["review"]})
    return rows


def research_error_rows(research: dict) -> list[dict]:
    """研报纠错对比行：左侧=研报写的（研报原文值），右侧=应为（正确值）。

    ★ 只取 status=FAIL 的确定性结论；「应为」取 ``expected_text``（无确定性结论时为「—」）。
      「研报原文值」取 ``left.raw`` —— 研报里真实写下的那串字符，不做任何改写。
    """
    rows = []
    for r in (research or {}).get("results") or []:
        if (r or {}).get("status") != "FAIL":
            continue
        cause = r.get("cause") or ""
        rows.append({
            "claim_id": r.get("claim_id"),
            "cause": cause,
            "cause_zh": CAUSE_ZH.get(cause, cause or "未归类"),
            "item": r.get("item_code") or "—",
            "period": str(r.get("period") or "—"),
            "written": (r.get("left") or {}).get("raw") or "—",
            "should": expected_text(r),
            "location": loc_str(r.get("evidence")),
            "reason": r.get("reason") or "",
        })
    return rows


def _short(text, n: int = 46) -> str:
    """过长原文截断显示（仅呈现层截断，完整原文见报告正文与网页①异常清单）。"""
    s = str(text if text is not None else "")
    return s if len(s) <= n else s[:n - 1] + "…"


# ---------------- 科普卡片（网页端 expander 与 HTML 报告共用同一份文案） ----------------

def _tint(hex_color: str, keep: float = 0.12) -> str:
    """色值混白得到同源淡色底：卡片底色与图上浓色属于**同一色系**，只是明度更浅。"""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    mix = lambda v: int(round(v * keep + 255 * (1 - keep)))          # noqa: E731
    return f"#{mix(r):02X}{mix(g):02X}{mix(b):02X}"


def _rgba(hex_color: str, alpha: float) -> str:
    """色值 → rgba(...)：Plotly 的填充色要透明度时用（与描边同源，保持同色系）。"""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def error_type_cards_html() -> str:
    """「研报常见错误类型科普」四张卡片（自包含 HTML，含样式）。

    ★ 卡片主色直接取 ``CAUSE_COLOR`` —— 与「研报纠错对比图」里同类错误的色块**同源**，
      读者在图上看到红色就是数值错误、琥珀色就是单位量级错误，无需再看图例。
    ★ 自包含：样式随片段一起下发，网页端 ``st.markdown`` 与 HTML 报告都能直接内嵌。
    """
    cards = []
    for cause, title, example, tip in ERROR_TYPE_CARDS:
        c = CAUSE_COLOR.get(cause, CAUSE_OTHER_COLOR)
        cards.append(
            f'<div class="fec-card" style="border-left:5px solid {c};'
            f'background:{_tint(c)}">'
            f'<div class="fec-head" style="color:{c}">{title}</div>'
            f'<div class="fec-ex">{example}</div>'
            f'<div class="fec-tip">{tip}</div></div>')
    return (
        '<style>'
        '.fec-wrap{display:flex;flex-wrap:wrap;gap:10px;margin:6px 0 2px}'
        '.fec-card{flex:1 1 240px;border-radius:8px;padding:10px 12px}'
        '.fec-head{font-weight:700;font-size:14px;margin-bottom:5px}'
        f'.fec-ex{{font-family:Consolas,Menlo,monospace;font-size:13px;'
        f'color:{PALETTE["ink"]};margin-bottom:5px}}'
        f'.fec-tip{{font-size:12px;color:{PALETTE["other"]};line-height:1.55}}'
        '</style>'
        '<div class="fec-wrap">' + "".join(cards) + '</div>')


# ---------------- Plotly（网页端 / HTML 报告） ----------------

def _go():
    """延迟导入 plotly：缺少该可选依赖时只影响图形，不影响报告其它内容。"""
    import plotly.graph_objects as go
    return go


_LAYOUT = dict(template="plotly_white",
               font=dict(family="Microsoft YaHei, PingFang SC, sans-serif", size=12))


def csi_radar(csi: dict):
    """CSI 五维雷达图。返回 ``plotly.graph_objects.Figure``；无维度数据时返回 None。"""
    dims = (csi or {}).get("dimensions") or {}
    keys = _ordered_dims(dims)
    if len(keys) < 3:
        return None
    go = _go()
    labels, scores, na_keys = [], [], []
    for k in keys:
        s = (dims[k] or {}).get("score")
        labels.append(f"{k}<br>{DIM_ZH.get(k, '')}")
        if s is None:
            na_keys.append(k)
            scores.append(0.0)
        else:
            scores.append(round(float(s) * 100, 1))

    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=scores + [scores[0]], theta=labels + [labels[0]], fill="toself",
        name="维度得分", line=dict(color=PALETTE["accent"], width=2),
        fillcolor=_rgba(PALETTE["accent"], 0.18),
        hovertemplate="%{theta}<br>得分 %{r}<extra></extra>"))
    if na_keys:
        # 不适用维度画在原点并单独成一条图例，避免被误读成「得分为 0 的不合格维度」
        fig.add_trace(go.Scatterpolar(
            r=[0] * len(na_keys), theta=[labels[keys.index(k)] for k in na_keys],
            mode="markers", name="不适用（权重已分摊）",
            marker=dict(color=PALETTE["axis"], size=10, symbol="x"),
            hovertemplate="%{theta}<br>不适用<extra></extra>"))
    fig.update_layout(
        **_LAYOUT,
        title=dict(text=f"一致性维度雷达图　CSI {csi.get('csi')}（等级 {csi.get('grade', '')}）",
                   font=dict(size=14), x=0.01, xanchor="left"),
        polar=dict(radialaxis=dict(visible=True, range=[0, 100],
                                   tickvals=[20, 40, 60, 80, 100])),
        showlegend=True, height=400, margin=dict(l=50, r=50, t=56, b=20))
    return fig


def _distribution_rows(rule_results: list[dict], research: dict, csi: dict | None) -> list[dict]:
    """异常分布图的行：数值一律来自单一口径，csi 只用来补全横轴类别（补齐 0 条维度）。"""
    rows = dimension_rows(rule_results, research)
    by = {r["dimension"]: r for r in rows}
    keys = _ordered_dims(set(by) | set((csi or {}).get("dimensions") or {}))
    return [by.get(k) or {"dimension": k, "label": DIM_ZH.get(k, k),
                          "fail": 0, "review": 0, "total": 0} for k in keys]


def anomaly_distribution(rule_results: list[dict], research: dict, csi: dict | None = None):
    """异常分布堆叠柱状图（按维度）。无异常亦无待复核时返回 None。

    ``csi`` 只用于补全横轴的维度类别，**不参与任何计数**。
    """
    n_fail, n_review = merged_anomaly_counts(rule_results, research)
    rows = _distribution_rows(rule_results, research, csi)
    if not rows or n_fail + n_review == 0:
        return None
    go = _go()
    labels = [f"{r['dimension']}<br>{r['label']}" for r in rows]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=labels, y=[r["fail"] for r in rows], name="异常（确定性）",
        marker_color=FAIL_COLOR,
        hovertemplate="%{x}<br>异常 %{y} 条<extra></extra>"))
    fig.add_trace(go.Bar(
        x=labels, y=[r["review"] for r in rows], name="待人工复核",
        marker_color=REVIEW_COLOR,
        hovertemplate="%{x}<br>待复核 %{y} 条<extra></extra>"))
    fig.update_layout(
        **_LAYOUT,
        title=dict(text="异常分布（按一致性维度）", font=dict(size=14),
                   x=0.01, xanchor="left"),
        barmode="stack", height=400, margin=dict(l=50, r=30, t=56, b=60),
        yaxis=dict(title="条数", rangemode="tozero"),
        annotations=[dict(
            text=f"合计：异常 {n_fail} 条 / 待复核 {n_review} 条（与报告总评同源）",
            xref="paper", yref="paper", x=1, y=1.12, xanchor="right", yanchor="top",
            showarrow=False, font=dict(size=11, color=PALETTE["other"]))])
    return fig


def research_error_compare(research: dict):
    """研报纠错对比图：逐条列出「研报写的」与「应为（正确值）」。无异常条目时返回 None。"""
    rows = research_error_rows(research)
    if not rows:
        return None
    go = _go()
    n = len(rows)
    fills = [
        [CAUSE_COLOR.get(r["cause"], CAUSE_OTHER_COLOR) for r in rows],  # 错误类型：色块即标签
        [_tint(PALETTE["fail"], 0.08)] * n,                             # 研报写的：异常色淡底
        [_tint("#1E7A46", 0.10)] * n,                                   # 应为：绿淡底
        ["#FFFFFF"] * n,
    ]
    fig = go.Figure(go.Table(
        columnwidth=[96, 300, 260, 210],
        header=dict(values=["错误类型", "研报写的（研报原文值）", "应为（正确值）", "位置"],
                    fill_color=PALETTE["accent"], align="left", height=30,
                    font=dict(color="#FFFFFF", size=12)),
        cells=dict(values=[[r["cause_zh"] for r in rows],
                           [_short(r["written"]) for r in rows],
                           [_short(r["should"]) for r in rows],
                           [_short(r["location"], 40) for r in rows]],
                   fill_color=fills, align="left", height=30,
                   font=dict(size=11, color=["#FFFFFF", PALETTE["ink"], "#1E7A46",
                                             PALETTE["muted"]]))))
    fig.update_layout(
        **_LAYOUT,
        title=dict(text="研报纠错对比：研报写的 → 应为（正确值）", font=dict(size=14),
                   x=0.01, xanchor="left"),
        height=150 + 34 * n, margin=dict(l=10, r=10, t=56, b=10))
    return fig


def figures_html(csi: dict, rule_results: list[dict], research: dict) -> str:
    """三张图嵌成一段可离线打开的 HTML 片段；plotly.js 只内联一次。

    缺 plotly 或缺数据时返回空串（报告生成不得因此失败）。
    """
    try:
        figs = [f for f in (csi_radar(csi), anomaly_distribution(rule_results, research, csi),
                            research_error_compare(research)) if f is not None]
    except Exception:  # noqa: BLE001 - 图形不可用不得影响报告正文
        return ""
    if not figs:
        return ""
    try:
        from plotly.offline import get_plotlyjs
        js = get_plotlyjs()
    except Exception:  # noqa: BLE001
        return ""
    parts = [f'<div class="viz"><script>{js}</script>']
    for f in figs:
        parts.append(f.to_html(full_html=False, include_plotlyjs=False,
                               config={"displayModeBar": False, "responsive": True}))
    parts.append("</div>")
    return "".join(parts)


# ---------------- reportlab.graphics（PDF 报告静态图） ----------------

def register_pdf_font() -> str:
    """注册中文 CID 字体并返回字体名。

    ★ reportlab 默认的 Helvetica / Times-Roman 无法编码中文，图形里的文字必须显式指定
      STSong-Light，否则会渲染成空白方框。重复调用无副作用。
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    try:
        pdfmetrics.getFont(PDF_FONT)
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont(PDF_FONT))
    return PDF_FONT


def _dims_and_scores(csi: dict):
    dims = (csi or {}).get("dimensions") or {}
    keys = _ordered_dims(dims)
    out = []
    for k in keys:
        s = (dims[k] or {}).get("score")
        out.append((k, None if s is None else float(s)))
    return out


def csi_radar_drawing(csi: dict, width: float = 330, height: float = 260):
    """PDF 用 CSI 雷达图（手绘，零新增依赖）。无维度数据时返回 None。"""
    from reportlab.lib import colors
    from reportlab.graphics.shapes import Drawing, Line, Polygon, String

    items = _dims_and_scores(csi)
    if len(items) < 3:
        return None
    font = register_pdf_font()
    n = len(items)
    cx, cy = width / 2.0, height / 2.0 - 6
    radius = min(width, height) * 0.33
    grid = colors.HexColor(PALETTE["grid"])
    d = Drawing(width, height)
    d.add(String(cx, height - 13, "一致性维度雷达图", fontName=font, fontSize=9.5,
                 textAnchor="middle", fillColor=colors.HexColor(PALETTE["ink"])))
    for frac in (0.2, 0.4, 0.6, 0.8, 1.0):
        pts = []
        for i in range(n):
            a = math.radians(90 - i * 360.0 / n)
            pts += [cx + radius * frac * math.cos(a), cy + radius * frac * math.sin(a)]
        d.add(Polygon(pts, strokeColor=grid, strokeWidth=0.4, fillColor=None))
    for i in range(n):
        a = math.radians(90 - i * 360.0 / n)
        d.add(Line(cx, cy, cx + radius * math.cos(a), cy + radius * math.sin(a),
                   strokeColor=grid, strokeWidth=0.4))
    pts, na_keys = [], []
    for i, (k, s) in enumerate(items):
        if s is None:
            na_keys.append(k)
        a = math.radians(90 - i * 360.0 / n)
        r = radius * (0.0 if s is None else s)
        pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
    d.add(Polygon(pts, strokeColor=colors.HexColor(PALETTE["accent"]), strokeWidth=1.4,
                  fillColor=colors.HexColor(PALETTE["soft"])))
    for i, (k, s) in enumerate(items):
        a = math.radians(90 - i * 360.0 / n)
        lx = cx + radius * 1.2 * math.cos(a)
        ly = cy + radius * 1.2 * math.sin(a)
        label = (f"{DIM_ZH.get(k, k)}（不适用）" if s is None
                 else f"{DIM_ZH.get(k, k)} {s * 100:.0f}")
        d.add(String(lx, ly - 3, label, fontName=font, fontSize=7.5, textAnchor="middle",
                     fillColor=colors.HexColor(
                         PALETTE["axis"] if s is None else PALETTE["ink"])))
    foot = f"CSI {csi.get('csi')}（等级 {csi.get('grade', '')}）"
    if na_keys:
        foot += f"；{'、'.join(na_keys)} 不适用，{NA_NOTE}"
    d.add(String(cx, 5, foot, fontName=font, fontSize=7, textAnchor="middle",
                 fillColor=colors.HexColor(PALETTE["muted"])))
    return d


def anomaly_distribution_drawing(rule_results: list[dict], research: dict,
                                 width: float = 340, height: float = 240,
                                 csi: dict | None = None):
    """PDF 用异常分布堆叠柱状图（手绘，零新增依赖）。无数据时返回 None。

    ``csi`` 只用于补全横轴的维度类别，**不参与任何计数**。
    """
    from reportlab.lib import colors
    from reportlab.graphics.shapes import Drawing, Line, Rect, String

    n_fail, n_review = merged_anomaly_counts(rule_results, research)
    rows = _distribution_rows(rule_results, research, csi)
    if not rows or n_fail + n_review == 0:
        return None
    font = register_pdf_font()
    x0, x1 = 40.0, width - 12.0
    # 标题独占一行；图例与合计另起一行，避免与标题在水平方向撞在一起
    y0, y1 = 34.0, height - 44.0
    top = max(r["total"] for r in rows)
    scale = (y1 - y0) / float(top)
    step = (x1 - x0) / float(len(rows))
    bar_w = min(26.0, step * 0.5)
    axis = colors.HexColor(PALETTE["axis"])
    d = Drawing(width, height)
    d.add(String(x0, height - 12, "异常分布（按一致性维度）", fontName=font, fontSize=9.5,
                 fillColor=colors.HexColor(PALETTE["ink"])))
    d.add(Line(x0, y0, x0, y1, strokeColor=axis, strokeWidth=0.6))
    d.add(Line(x0, y0, x1, y0, strokeColor=axis, strokeWidth=0.6))
    d.add(String(x0 - 3, y1 - 8, str(top), fontName=font, fontSize=7, textAnchor="end",
                 fillColor=colors.HexColor(PALETTE["muted"])))
    d.add(String(x0 - 3, y0 - 3, "0", fontName=font, fontSize=7, textAnchor="end",
                 fillColor=colors.HexColor(PALETTE["muted"])))
    for i, r in enumerate(rows):
        cxi = x0 + step * (i + 0.5)
        h_fail = r["fail"] * scale
        h_rev = r["review"] * scale
        if h_fail:
            d.add(Rect(cxi - bar_w / 2, y0, bar_w, h_fail,
                       fillColor=colors.HexColor(FAIL_COLOR), strokeColor=None))
        if h_rev:
            d.add(Rect(cxi - bar_w / 2, y0 + h_fail, bar_w, h_rev,
                       fillColor=colors.HexColor(REVIEW_COLOR), strokeColor=None))
        d.add(String(cxi, y0 + h_fail + h_rev + 3, f"{r['fail']} / {r['review']}",
                     fontName=font, fontSize=7, textAnchor="middle",
                     fillColor=colors.HexColor(PALETTE["ink"])))
        d.add(String(cxi, y0 - 11, r["dimension"], fontName=font, fontSize=7.5,
                     textAnchor="middle", fillColor=colors.HexColor(PALETTE["ink"])))
        d.add(String(cxi, y0 - 20, r["label"], fontName=font, fontSize=6.5,
                     textAnchor="middle", fillColor=colors.HexColor(PALETTE["muted"])))
    d.add(Rect(x0, height - 29, 8, 8, fillColor=colors.HexColor(FAIL_COLOR), strokeColor=None))
    d.add(String(x0 + 11, height - 28, "异常（确定性）", fontName=font, fontSize=7,
                 fillColor=colors.HexColor(PALETTE["ink"])))
    d.add(Rect(x0 + 78, height - 29, 8, 8, fillColor=colors.HexColor(REVIEW_COLOR),
               strokeColor=None))
    d.add(String(x0 + 89, height - 28, "待人工复核", fontName=font, fontSize=7,
                 fillColor=colors.HexColor(PALETTE["ink"])))
    d.add(String(x1, height - 28, f"合计：异常 {n_fail} / 待复核 {n_review}",
                 fontName=font, fontSize=7, textAnchor="end",
                 fillColor=colors.HexColor(PALETTE["muted"])))
    return d
