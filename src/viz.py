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

from src.report import DIM_ZH, expected_text, loc_str, merged_anomaly_counts

# 维度顺序固定：与 csi.json / report.py 的维度命名一致（C内/C间/C期/C附/C研）
DIM_ORDER = ("C内", "C间", "C期", "C附", "C研")

# 研报引用错误的归因枚举（定义见 src/research_check.py）
CAUSE_ZH = {"VALUE_ERROR": "数值错误", "UNIT_SCALE": "单位量级错误",
            "PERIOD_MISMATCH": "期间不符", "SIGN_ERROR": "正负号错误"}
CAUSE_COLOR = {"VALUE_ERROR": "#C0392B", "UNIT_SCALE": "#B7791F",
               "PERIOD_MISMATCH": "#2D6CDF", "SIGN_ERROR": "#7B4FA0"}
CAUSE_OTHER_COLOR = "#5B6B7C"

FAIL_COLOR = "#C0392B"
REVIEW_COLOR = "#E0A33C"
NA_NOTE = "不适用维度的权重已按比例分摊至其余维度"
PDF_FONT = "STSong-Light"


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
    # review 段：整段视为待复核，维度缺省按研报维度 C研 计
    for r in list(research.get("review") or []):
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
        name="维度得分", line=dict(color="#2D6CDF", width=2),
        fillcolor="rgba(45,108,223,0.18)",
        hovertemplate="%{theta}<br>得分 %{r}<extra></extra>"))
    if na_keys:
        # 不适用维度画在原点并单独成一条图例，避免被误读成「得分为 0 的不合格维度」
        fig.add_trace(go.Scatterpolar(
            r=[0] * len(na_keys), theta=[labels[keys.index(k)] for k in na_keys],
            mode="markers", name="不适用（权重已分摊）",
            marker=dict(color="#8A93A0", size=10, symbol="x"),
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
            showarrow=False, font=dict(size=11, color="#5B6B7C"))])
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
        ["#FDECEA"] * n,                                                # 研报写的：红底
        ["#E9F7EF"] * n,                                                # 应为：绿底
        ["#FFFFFF"] * n,
    ]
    fig = go.Figure(go.Table(
        columnwidth=[96, 300, 260, 210],
        header=dict(values=["错误类型", "研报写的（研报原文值）", "应为（正确值）", "位置"],
                    fill_color="#2D6CDF", align="left", height=30,
                    font=dict(color="#FFFFFF", size=12)),
        cells=dict(values=[[r["cause_zh"] for r in rows],
                           [_short(r["written"]) for r in rows],
                           [_short(r["should"]) for r in rows],
                           [_short(r["location"], 40) for r in rows]],
                   fill_color=fills, align="left", height=30,
                   font=dict(size=11, color=["#FFFFFF", "#333333", "#1E7A46", "#666666"]))))
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
    grid = colors.HexColor("#D8DCE2")
    d = Drawing(width, height)
    d.add(String(cx, height - 13, "一致性维度雷达图", fontName=font, fontSize=9.5,
                 textAnchor="middle", fillColor=colors.HexColor("#333333")))
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
    d.add(Polygon(pts, strokeColor=colors.HexColor("#2D6CDF"), strokeWidth=1.4,
                  fillColor=colors.HexColor("#CFE0F7")))
    for i, (k, s) in enumerate(items):
        a = math.radians(90 - i * 360.0 / n)
        lx = cx + radius * 1.2 * math.cos(a)
        ly = cy + radius * 1.2 * math.sin(a)
        label = (f"{DIM_ZH.get(k, k)}（不适用）" if s is None
                 else f"{DIM_ZH.get(k, k)} {s * 100:.0f}")
        d.add(String(lx, ly - 3, label, fontName=font, fontSize=7.5, textAnchor="middle",
                     fillColor=colors.HexColor("#8A93A0" if s is None else "#333333")))
    foot = f"CSI {csi.get('csi')}（等级 {csi.get('grade', '')}）"
    if na_keys:
        foot += f"；{'、'.join(na_keys)} 不适用，{NA_NOTE}"
    d.add(String(cx, 5, foot, fontName=font, fontSize=7, textAnchor="middle",
                 fillColor=colors.HexColor("#666666")))
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
    axis = colors.HexColor("#9AA3AE")
    d = Drawing(width, height)
    d.add(String(x0, height - 12, "异常分布（按一致性维度）", fontName=font, fontSize=9.5,
                 fillColor=colors.HexColor("#333333")))
    d.add(Line(x0, y0, x0, y1, strokeColor=axis, strokeWidth=0.6))
    d.add(Line(x0, y0, x1, y0, strokeColor=axis, strokeWidth=0.6))
    d.add(String(x0 - 3, y1 - 8, str(top), fontName=font, fontSize=7, textAnchor="end",
                 fillColor=colors.HexColor("#666666")))
    d.add(String(x0 - 3, y0 - 3, "0", fontName=font, fontSize=7, textAnchor="end",
                 fillColor=colors.HexColor("#666666")))
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
                     fillColor=colors.HexColor("#333333")))
        d.add(String(cxi, y0 - 11, r["dimension"], fontName=font, fontSize=7.5,
                     textAnchor="middle", fillColor=colors.HexColor("#333333")))
        d.add(String(cxi, y0 - 20, r["label"], fontName=font, fontSize=6.5,
                     textAnchor="middle", fillColor=colors.HexColor("#888888")))
    d.add(Rect(x0, height - 29, 8, 8, fillColor=colors.HexColor(FAIL_COLOR), strokeColor=None))
    d.add(String(x0 + 11, height - 28, "异常（确定性）", fontName=font, fontSize=7,
                 fillColor=colors.HexColor("#333333")))
    d.add(Rect(x0 + 78, height - 29, 8, 8, fillColor=colors.HexColor(REVIEW_COLOR),
               strokeColor=None))
    d.add(String(x0 + 89, height - 28, "待人工复核", fontName=font, fontSize=7,
                 fillColor=colors.HexColor("#333333")))
    d.add(String(x1, height - 28, f"合计：异常 {n_fail} / 待复核 {n_review}",
                 fontName=font, fontSize=7, textAnchor="end",
                 fillColor=colors.HexColor("#666666")))
    return d
