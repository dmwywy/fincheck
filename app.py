# app.py —— 网页入口
"""FinCheck 财报一致性核查智能体 · 网页界面。

运行：streamlit run app.py

页面顶部可上传财报并启动一次核查；其余结果视图仅读取 runs/<run_id>/ 下的落盘产物。
离线模式不发网络请求，在线模式才调用已配置的模型 API。
"""
import json
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

# CSI 口径提示：与报告三格式共用同一份文案，避免"满分 = 全部维度通过"的误导
# 「异常 / 待复核」计数同样与报告总评同源，见 src.report.merged_anomaly_counts
from src.report import (PARKED_BUCKETS, _csi_caveat, _csi_exec_note, expected_text,
                        loc_str, merged_anomaly_counts,
                        research_parked_rows, research_review_groups, review_entries,
                        unprocessed_entries)

ROOT = Path(__file__).resolve().parent
STATUS_ZH = {"PASS": "通过", "FAIL": "异常", "REVIEW": "需人工复核", "NA": "不适用"}
DIM_ZH = {"C内": "表内勾稽", "C间": "表间一致", "C期": "跨期衔接",
          "C附": "主表与附注", "C研": "研报引用"}
DISCLAIMER = "本系统输出为财务数据一致性核查结果，不构成投资建议，所有结论需人工复核。"

st.set_page_config(page_title="FinCheck · 财报一致性核查", layout="wide")


def _read(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def list_runs() -> list[str]:
    d = ROOT / "runs"
    if not d.exists():
        return []
    return sorted((p.name for p in d.iterdir() if p.is_dir()), reverse=True)


st.title("FinCheck · 财报一致性核查智能体")
st.markdown("##### 让普通投资者看得懂、查得清：逐条核对研报与财报，并教你识别研报常见错误")
st.caption("把研报与财报摆在一起逐条对：程序按确定性规则核对表内勾稽、跨期衔接与研报引用，"
          "指出「研报写的」与「财报实际的」差在哪里、该项应为的正确写法；"
          "并教你识别研报常见的四类错误（正负号、单位量级、期间、数值）。")
st.warning(DISCLAIMER)

with st.container(border=True):
    st.subheader("上传材料并开始核查")
    st.caption("拖拽或选择年报 / 半年报 PDF / DOCX。若文件名没有年份，请填写报告年度；半年度报告请把报告期间选为「半年报」。")
    with st.form("report_upload_form"):
        uploaded_files = st.file_uploader(
            "选择报告文件", type=["pdf", "docx", "docm"],
            accept_multiple_files=True, max_upload_size=300,
            help="支持一份或多份报告；同一批上传的报告需属于同一种期间类型。",
        )
        year_default = st.number_input(
            "文件名没有年份时使用的报告年度", min_value=2000, max_value=2100,
            value=2025, step=1,
        )
        period_label = st.selectbox(
            "报告期间", ("年报", "半年报"), index=0,
            help="半年报：利润表、现金流量表按年初至 6 月 30 日累计（YTD），"
                 "资产负债表期末余额取 6 月 30 日。选错期间会让半年报被按年报列头抽取、结论失真。",
        )
        offline_mode = st.checkbox(
            "离线运行（不调用模型，只使用本地缓存）", value=True,
            help="离线缓存未命中时，语义任务转人工复核；规则核查和报告仍会继续。取消勾选会使用已配置的模型 API。",
        )
        api_key_input = st.text_input(
            "DeepSeek API Key（可选，留空则用 .env 里已配置的密钥）", type="password",
            help="取消勾选离线运行后，模型调用需要 Key。此处填的 Key 仅用于本次运行构造请求，"
                 "不写入日志、报告或缓存；留空时读取项目根目录 .env 的 DEEPSEEK_API_KEY。",
        )
        submitted = st.form_submit_button(
            "开始核查并生成报告", type="primary", icon=":material/fact_check:",
        )

    if submitted:
        if not uploaded_files:
            st.error("请先选择至少一份年报文件。")
        else:
            run_id = f"web_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
            try:
                from src.web import run_uploaded_reports

                with st.status("正在处理上传材料…", expanded=True) as progress:
                    outcome = run_uploaded_reports(
                        uploaded_files, run_id=run_id, default_year=int(year_default),
                        offline=offline_mode,
                        report_period="half_year" if period_label == "半年报" else "annual",
                        api_key=(api_key_input or None),
                        on_progress=progress.write,
                    )
                    progress.update(label="核查完成，报告已生成", state="complete")

                st.session_state["selected_run_id"] = run_id
                st.session_state["run_selection"] = run_id
                st.success(f"本次运行编号：`{run_id}`。字段与核查产物保存在 `runs/{run_id}/`。")
                st.write("报告文件：" + " · ".join(
                    f"{fmt}: `{path}`" for fmt, path in outcome["reports"].items()
                ))
                st.rerun()
            except Exception as exc:  # noqa: BLE001 - show a useful failure in the UI
                st.error(f"核查未完成：{type(exc).__name__}: {exc}")
                st.caption(f"已上传文件保存在 `runs/{run_id}/uploaded/`，可检查后重新运行。")

runs = list_runs()
if not runs:
    st.info("还没有历史运行结果。可在上方上传年报开始核查，也可使用命令行入口。")
    st.stop()

default_run = st.session_state.get("selected_run_id")
default_index = runs.index(default_run) if default_run in runs else 0
run_id = st.sidebar.selectbox(
    "选择运行编号", runs, index=default_index, key="run_selection",
    help="每个运行编号对应 runs/<run_id>/ 下的一个历史运行目录。选中哪一个，"
         "右侧所有指标与明细就读哪一个目录的落盘产物；上传新文件后会自动切到本次运行。")
st.sidebar.caption("运行编号 = 一次核查的目录名（含上传件、提取字段、核查结果、报告）。")

base = ROOT / "runs" / run_id
# ★ 读产物前先做新鲜度检查：产物由**旧版代码**产出时（服务进程常驻内存、改了代码没
#   重启），用当前代码重算确定性部分，避免页面把已经修好的结论按旧的再显示一遍。
from src.refresh import ensure_fresh
_fresh = ensure_fresh(run_id)
manifest = _read(base / "manifest.json", {}) or {}
results = (_read(base / "rules" / "results.json", {}) or {}).get("results", [])
csi = _read(base / "rules" / "csi.json", {}) or {}
research = _read(base / "rules" / "research_claims.json", {}) or {}
mappings = (_read(base / "llm" / "mappings.json", {}) or {}).get("mappings", [])
# 语义审计结论（RC06）：模型提示的疑似问题，独立存放、不计入异常口径
from src.semantic_audit import load_findings
llm_audit = load_findings(run_id)
# 模型解释单独存放（结果文件保持纯确定性），按编号关联回来展示
_expl = {i.get("claim_id") or i.get("rule_id"): (i.get("explanation") or {})
         for i in (_read(base / "rules" / "explanations.json", {}) or {}).get("items", [])}
for _r in list(results) + list(research.get("results", [])):
    _e = _expl.get(_r.get("claim_id") or _r.get("rule_id"))
    if _e:
        _r["explanation"] = _e

fields = []
for p in sorted((base / "ingested").glob("*.json")) if (base / "ingested").exists() \
        else sorted((ROOT / "data" / "structured").glob("*.json")):
    payload = _read(p)
    if isinstance(payload, list):
        fields.extend(r for r in payload if isinstance(r, dict) and "item_code" in r)

# ---- 顶部指标卡 ----
calls = manifest.get("calls", {})
# ★ 「异常 / 待复核」必须与 PDF/Excel/网页报告的总评同源：规则级结果在
#   rules/results.json，研报引用结果在 rules/research_claims.json（results + review 两段）。
#   只统计前者时网页显示 0，与报告里的「不一致 3 条、待复核 41 条」对不上。
n_fail, n_review = merged_anomaly_counts(results, research)
anomalies = [r for r in results if r.get("status") in ("FAIL", "REVIEW")]
c1, c2, c3, c4, c5 = st.columns(5)
_csi_caveat_text = _csi_caveat(csi)
c1.metric("CSI（临时）" if _csi_caveat_text else "CSI",
          csi.get("csi") if csi.get("csi") is not None else "—",
          csi.get("grade_label", ""), delta_color="off")
c2.metric("提取字段", len(fields))
c3.metric("规则结果", len(results))
c4.metric("异常 / 待复核", f"{n_fail} / {n_review}",
          help="异常 = 规则级 FAIL + 研报引用 FAIL；待复核 = 规则级 REVIEW + 研报引用 REVIEW。"
               "两段研报比对结果存放在 rules/research_claims.json，与报告总评口径一致。")
c5.metric("模型调用", f"{calls.get('api_ok', 0)} 在线 / {calls.get('cache_hit', 0)} 缓存")
if _csi_caveat_text:
    st.warning(f"CSI 口径提示：{_csi_caveat_text}")
st.caption("指标卡读的是**左侧选中的历史运行**的统计，与上传表单里是否勾选离线无关。"
           "「模型调用」= 在线（api_ok，真实请求成功）/ 缓存（cache_hit，命中本地缓存）。"
           "离线运行、或该次全部命中缓存时，都会显示为 0 在线。")

# 代码版本提示：产物由旧版代码产出时必须显著告警，否则"改了代码结论没变"会被误读成
# 系统没有修正能力（实测踩过：C研 方向规则已修好，页面仍显示旧结论）。
if _fresh.get("stale"):
    if _fresh.get("recomputed"):
        st.warning("⚠ 本次运行的落盘产物由**非当前版本**的源码产出（通常是服务进程未重启）。"
                   "已用当前代码重算研报引用核查结论，页面与报告显示的是重算后的结果；"
                   "建议重启服务后重跑本批材料，以获得完全一致的产物。")
    else:
        st.error("⚠ 本次运行的落盘产物由**非当前版本**的源码产出（通常是服务进程未重启），"
                 "且缺少可复算的中间产物，页面显示的研报引用结论可能落后于当前代码。"
                 "请重启服务后重新上传材料运行。")

# 未参与核查的文件必须显式提示（未识别 / 处理失败 / 识别到但没抽出内容）：
# 否则用户以为它已经被核查过了。判据与报告共用 src.report.unprocessed_entries。
_ingest_log = _read(base / "ingest_log.json", []) or []
_unrecognized = unprocessed_entries(_ingest_log)
if _unrecognized:
    st.warning("以下上传文件**未参与字段提取与一致性核查**："
               + "；".join(f"{e.get('file')}（{e.get('action')}）" for e in _unrecognized))

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["① 异常清单", "② 一致性指标", "③ 提取字段", "④ 规则明细", "⑤ 报告下载"],
    key="results_tabs", on_change="rerun",
)

# ---- ① 异常清单 ----
with tab1:
    rf = [x for x in research.get("results", []) if x.get("status") == "FAIL"]
    # ★ 只取**计入待复核口径**的条目（review_entries），与指标卡、报告总评同一过滤；
    #   预测值/口径不可比两桶单列展示、不计入计数（见下）。
    rv = review_entries(research)
    _rv_groups = research_review_groups({"research": research})
    _parked = [(key, label, why, research_parked_rows({"research": research}, key))
               for key, label, why in PARKED_BUCKETS]
    _parked = [g for g in _parked if g[3]]
    if not anomalies and not rf and not rv and not _parked:
        st.success("本次运行未发现异常。")
    else:
        for r in anomalies:
            ev = r.get("evidence") or {}
            loc = loc_str(ev)
            icon = "🔴" if r.get("status") == "FAIL" else "🟡"
            with st.expander(f"{icon} {r.get('rule_id')}｜"
                             f"{DIM_ZH.get(r.get('dimension'), r.get('dimension'))}｜"
                             f"{STATUS_ZH.get(r.get('status'), r.get('status'))}"
                             f"｜{r.get('period', '')}"):
                st.markdown(f"**计算式**：`{r.get('expr') or '—'}`")
                a, b, c = st.columns(3)
                a.metric("差异", r.get("diff") or "—")
                b.metric("容差", r.get("tolerance") or "—")
                c.metric("严重程度", r.get("severity") or "—")
                if r.get("reason"):
                    st.write(f"**原因**：{r['reason']}")
                ex = r.get("explanation") or {}
                if ex.get("text"):
                    flag = "" if ex.get("agrees_with_program", True) else " ⚠与程序归因不一致"
                    st.write(f"**模型解释**（{ex.get('cause', '')}，置信度 {ex.get('confidence')}）{flag}："
                             f"{ex['text']}")
                st.write(f"**证据定位**：{ev.get('file') or '—'} · {loc}")
                if ev.get("raw_text"):
                    st.code(ev["raw_text"], language=None)

    if rf:
        st.subheader("研报引用异常")
        st.dataframe(pd.DataFrame([{
            "状态": x.get("status"), "科目": x.get("item_code") or "—",
            "期间": str(x.get("period") or "—"),
            "研报原文值": (x.get("left") or {}).get("raw"),
            "财报值": (x.get("right") or {}).get("value"),
            "差异": x.get("diff"), "应为（正确值）": expected_text(x),
            "归因": x.get("cause"), "说明": x.get("reason"),
            "位置": loc_str(x.get("evidence")),
        } for x in rf]), width='stretch')

    _RV_COLS = ["科目", "期间", "原因", "研报原句", "应为（正确值）", "位置"]
    if rv:
        st.subheader(f"研报引用待人工复核（{len(rv)} 条）")
        st.caption("程序无法自动判定，不代表存在错误。这些条目计入上方的"
                   "「异常 / 待复核」指标卡，与报告总评口径一致；"
                   "下面按**程序为什么判不了**分组列出，便于逐类核对。")
        _reasons = pd.Series([x.get("reason", "") for x in rv]).value_counts()
        st.dataframe(pd.DataFrame({
            "原因": _reasons.index, "条数": _reasons.values}), width='stretch', hide_index=True)
        for _zh, _rows in _rv_groups:
            with st.expander(f"{_zh}（{len(_rows)} 条）", expanded=len(_rows) >= 5):
                st.dataframe(pd.DataFrame(_rows, columns=_RV_COLS),
                             width='stretch', hide_index=True)

    # 两个单列桶：**不计入待复核计数**，但必须显式展示，否则读者以为条目凭空消失。
    for _key, _label, _why, _rows in _parked:
        st.subheader(f"{_label}（{len(_rows)} 条，不计入待复核计数）")
        st.caption(_why)
        st.dataframe(pd.DataFrame(_rows, columns=_RV_COLS), width='stretch', hide_index=True)

    # 模型语义审计提示（RC06）：程序规则覆盖不到的语义型问题，由模型不限类型地指出。
    # ★ 与上方异常分开呈现：这些结论不可复现，是"提示"而非"判定"，不计入异常口径。
    if llm_audit:
        st.subheader(f"模型语义审计提示（{len(llm_audit)} 条，不计入异常计数）")
        st.caption("由大模型对研报原文做不限类型的自由审计后给出，用于补足确定性规则覆盖"
                   "不到的语义型问题（同比方向、期间语义、单位量级、研报内部预测表勾稽等）。"
                   "程序只校验了它引用的原句真实存在，未对结论做数值判定，需人工确认。")
        st.dataframe(pd.DataFrame([{
            "问题类型": x.get("problem_type") or "—", "归因": x.get("cause") or "—",
            "研报原句": (x.get("evidence") or {}).get("quote")
                        or (x.get("evidence") or {}).get("sentence") or "—",
            "位置": loc_str(x.get("evidence")),
            "模型说明": x.get("reason", ""), "提示应为": x.get("expected_value") or "—",
        } for x in llm_audit]), width='stretch', hide_index=True)

# ---- ② 一致性指标 ----
with tab2:
    # ---- 指标卡（口径与报告总评同源，不得另起一套统计） ----
    _processed_files = max(0, len(_ingest_log) - len(_unrecognized))
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("CSI 总分", csi.get("csi") if csi.get("csi") is not None else "—",
              csi.get("grade_label", ""), delta_color="off")
    k2.metric("发现异常", n_fail,
              help="规则级 FAIL + 研报引用 FAIL。计数口径与报告总评、①异常清单完全一致。")
    k3.metric("待人工复核", n_review,
              help="规则级 REVIEW + 研报引用 REVIEW 与待复核清单。计数口径与报告总评完全一致。")
    k4.metric("已核查文件", _processed_files,
              help="成功参与字段提取与核查的上传文件数，不含未识别 / 处理失败 / 未抽出内容的文件。")

    # ---- 核查图表（与报告三端同源；只做呈现，不做数值计算） ----
    from src.viz import (anomaly_distribution, csi_radar, error_type_cards_html,
                         research_error_compare)
    dims = csi.get("dimensions") or {}
    st.subheader("核查图表")
    _fig_r, _fig_d = csi_radar(csi), anomaly_distribution(results, research, csi)
    if _fig_r or _fig_d:
        _vc1, _vc2 = st.columns(2)
        if _fig_r:
            _vc1.plotly_chart(_fig_r, key="viz_radar")
            _vc1.caption(_csi_exec_note(csi))
        if _fig_d:
            _vc2.plotly_chart(_fig_d, key="viz_dist")
            _vc2.caption("异常与待复核的计数与报告总评、①异常清单完全同源（含 0 条的维度）。")
    else:
        st.info("本次运行没有异常或待复核条目，也没有可执行的 CSI 维度，未生成图表。")
    _fig_e = research_error_compare(research)
    if _fig_e:
        st.plotly_chart(_fig_e, key="viz_err")
        st.caption("「研报写的」= 研报原文值；「应为（正确值）」= 系统核出的正确写法"
                   "（无确定性结论时为「—」）。过长原文已截断，完整原文与证据定位见①异常清单。")

    # 「研报常见错误类型科普」：卡片配色与上方对比图的「错误类型」列同源（见 src.viz.PALETTE）。
    # ★ 这一块把作品从"核查工具"扩展为"核查 + 教学"，直接点题智慧教育。
    with st.expander("教你识别研报常见错误（四类错误的识别要点）", expanded=False):
        st.markdown(error_type_cards_html(), unsafe_allow_html=True)

    st.divider()
    if not dims:
        st.info("本次运行没有可执行的规则，未计算 CSI。")
    else:
        rows = []
        for k, v in sorted(dims.items()):
            rows.append({
                "维度": k, "含义": DIM_ZH.get(k, ""),
                # 统一转成字符串：该列会混"数字"与"不适用"，列类型不一致时
                # Streamlit 底层的 pyarrow 会抛 ArrowInvalid（虽被吞掉，但表格渲染异常）
                "得分": "不适用" if v["score"] is None else f"{v['score'] * 100:.1f}",
                "可执行": v["rules_applicable"], "通过": v["pass"],
                "异常": v["fail"], "待复核": v["review"], "不适用": v["na"],
            })
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
        st.markdown(
            f"**综合一致性指数 CSI = {csi.get('csi')}　"
            f"等级 {csi.get('grade')}（{csi.get('grade_label')}）**\n\n"
            f"- 公式：`CSI = Σ(维度得分 × 归一化权重) × 100`\n"
            f"- 维度得分 = `(通过数 + 0.5 × 待复核数) ÷ 可执行规则数`\n"
            f"- 原权重：`{json.dumps(csi.get('weights_used', {}), ensure_ascii=False)}`\n"
            f"- 归一化权重：`{json.dumps(csi.get('weights_normalized', {}), ensure_ascii=False)}`\n"
            f"- 不适用维度：`{','.join(csi.get('na_dimensions') or []) or '无'}`（权重已分摊）")
        if _csi_caveat_text:
            st.warning(f"CSI 口径提示：{_csi_caveat_text}")

# ---- ③ 提取字段 ----
with tab3:
    if not fields:
        st.info("没有提取到字段记录。")
    else:
        df = pd.DataFrame([{
            "科目编号": r.get("item_code"), "原文科目": r.get("item_raw"),
            "期间": r.get("period"), "期别": r.get("period_type"),
            "数值": r.get("value"), "单位": r.get("unit_raw"),
            "定位": r.get("page") if r.get("page") is not None else r.get("locator"),
            "原文片段": r.get("raw_text"),
        } for r in fields])
        st.caption(f"共 {len(df)} 条。可按列排序、筛选。")
        st.dataframe(df, width='stretch', hide_index=True)
        st.download_button("下载为 CSV", df.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"{run_id}_fields.csv", mime="text/csv")

# ---- ④ 规则明细 ----
with tab4:
    if not results:
        st.info("没有规则核查结果。")
    else:
        df = pd.DataFrame([{
            "状态": STATUS_ZH.get(r.get("status"), r.get("status")),
            "规则编号": r.get("rule_id"), "维度": r.get("dimension"),
            "期间": r.get("period"), "期别": r.get("period_type"),
            "表达式": r.get("expr"), "差异": r.get("diff"), "容差": r.get("tolerance"),
            "原因": r.get("reason"),
        } for r in results])
        picks = st.multiselect("筛选状态", sorted(df["状态"].unique()),
                               default=sorted(df["状态"].unique()))
        st.dataframe(df[df["状态"].isin(picks)], width='stretch', hide_index=True)

    if mappings:
        with st.expander(f"科目归一化结果（{len(mappings)} 条）"):
            st.dataframe(pd.DataFrame([{
                "财报原文科目": m.get("raw_header"), "标准科目编号": m.get("canonical_item"),
                "置信度": m.get("confidence"),
                "来源": "；".join(f"p{s.get('page')}" if s.get("page") is not None
                                else (s.get("locator") or "")
                                for s in (m.get("evidence") or {}).get("sources", [])),
            } for m in mappings]), width='stretch', hide_index=True)

# ---- ⑤ 报告下载 ----
with tab5:
    st.write("三种形式的核查报告（内容一致，用途不同）：")
    items = [("report.pdf", "PDF 正式报告", "application/pdf"),
             ("report.xlsx", "Excel 异常清单",
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
             ("report.html", "网页报告", "text/html")]
    found = False
    for name, label, mime in items:
        p = base / name
        if not p.exists():
            continue
        found = True
        # ★ 只有本页签打开时才读报告字节并建下载按钮（报告可能较大，每次 rerun
        #   都读盘没必要）。但"有没有报告"的判断与提示**不能**一起被门控——
        #   否则未打开该页签时，没有报告的运行连一句提示都不显示（测试即因此失败）。
        if tab5.open:
            st.download_button(f"⬇ {label}（{p.stat().st_size // 1024} KB）",
                               p.read_bytes(), file_name=f"{run_id}_{name}", mime=mime,
                               key=name)
    if not found:
        st.info("本次运行还没生成报告。执行：\n\n"
                f"```\npython -m fincheck report --run-id {run_id}\n```")

    st.divider()
    st.caption("运行信息")
    st.json({"run_id": run_id,
             "模型": (manifest.get("model") or {}).get("model_id"),
             "提示词哈希": {p["prompt_id"]: p["sha256"][:16] for p in manifest.get("prompts", [])},
             "调用统计": calls})
