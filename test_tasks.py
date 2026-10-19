# test_tasks.py   运行：python -m pytest -q
import json
from pathlib import Path

from src.errors import classify
from src.schemas import SCHEMA_BY_TASK
from src.inputs import (build_match_item,
                        build_match_claim, build_explain_anomaly)


def test_model_whitelist():
    import src.llm as L
    assert L.MODEL_ID in {"deepseek-flash", "deepseek-v4-pro"}


def test_temperature_fixed():
    import src.llm as L
    assert L.PARAMS["temperature"] == 0


def test_schema_count_and_export():
    assert len(SCHEMA_BY_TASK) == 5
    for cls in SCHEMA_BY_TASK.values():
        cls.model_json_schema()


def test_inputs_constructors():
    p = build_match_item(["营业收入"], [{"code": "REVENUE", "name": "营业收入"}])
    assert p["task"] == "match_item"
    for f in (build_match_claim, build_explain_anomaly):
        assert callable(f)


def test_error_classify():
    E = type("E", (Exception,), {"status_code": 401})
    retry, hint, code = classify(E())
    assert retry is False and code == 401


def test_prompt_files_naming():
    """四个提示词必须都能按约定文件名加载"""
    from src.tasks import load_prompt
    for task in SCHEMA_BY_TASK:
        text, meta = load_prompt(task)
        assert "json" in text.lower(), f"{task} 提示词缺少 json 字样"
        assert len(meta["sha256"]) == 64


def test_skills_cover_registered_tasks():
    """skills/ 下每个 SKILL.md 必须覆盖全部注册任务，且含比赛要求的 4 个能力 Skill。"""
    skills_dir = Path(__file__).resolve().parent / "skills"
    names = {p.parent.name for p in skills_dir.glob("*/SKILL.md")}
    # ① 模型侧 4 个受注册任务，② 比赛要求的能力 Skill（计算/核查/提取/报告）
    required = set(SCHEMA_BY_TASK) | {"financial-ratio-calculator", "consistency-checker",
                                      "field-extractor", "report-generator"}
    assert required <= names, f"skills 缺少以下定义：{required - names}"


def test_netguard_installed():
    """白名单只拦对外地址，且**本机回环必须放行**——否则网页界面直接不可用。"""
    from src.netguard import (ALLOW_HOST, install_allowlist, is_allowed,
                              uninstall_allowlist)
    assert ALLOW_HOST == "api.deepseek.com"
    assert is_allowed("api.deepseek.com", 443)
    assert is_allowed("127.0.0.1", 8899)          # Streamlit 本地服务
    assert is_allowed("localhost", 80)
    assert not is_allowed("evil.example.com", 443)
    assert not is_allowed("api.deepseek.com", 80)  # 端口也要对

    install_allowlist(run_id="pytest")
    try:
        import socket
        socket.socket().connect(("127.0.0.1", 9))   # 回环放行（连不上是另一回事）
    except PermissionError:
        raise AssertionError("netguard 拦截了本机回环，会搞垮网页界面")
    except OSError:
        pass                                        # 连接被拒/超时都正常
    finally:
        uninstall_allowlist()                       # 必须还原，否则污染后续测试


def test_post_checks_enum_whitelist():
    """模型返回越界的 canonical_item 应被三层校验拦下（无需联网）。"""
    from src.tasks import _post_checks
    bad = {"items": [{"raw_header": "某科目", "canonical_item": "NOT_A_CODE",
                      "confidence": 0.9}]}
    errs = _post_checks(bad, "match_item", None)
    assert any("越界" in e for e in errs)


def test_post_checks_evidence():
    """模型引用一个它没见过的 chunk_id，应判证据无效。"""
    from src.tasks import _post_checks
    bad = {"evidence": {"chunk_id": "p999_t1", "quote": "营业收入"}}
    errs = _post_checks(bad, "explain_anomaly", {"chunks": {"p042_t1": "营业收入"}})
    assert any("chunk_id" in e for e in errs)


def test_post_checks_empty_item_code_ok():
    """模型判"无法判断"时 item_code 为空字符串，不应被判越界。"""
    from src.tasks import _post_checks
    ok = {"matches": [{"claim_id": "c01", "item_code": "", "period": "",
                       "confidence": 0.2}]}
    assert _post_checks(ok, "match_claim", None) == []


def test_program_evidence_tasks_skip_evidence_check():
    """证据由程序回填的任务，不应因缺 evidence 被判失败。"""
    from src.tasks import PROGRAM_EVIDENCE_TASKS, _post_checks
    assert {"match_item", "match_claim"} <= PROGRAM_EVIDENCE_TASKS
    for task in PROGRAM_EVIDENCE_TASKS:
        assert _post_checks({}, task, {"chunks": {"p001_t1": "x"}}) == []


def test_claim_fields_carries_no_amounts():
    """interfaces.md §5.1：给模型的字标清单只含科目与期间，不得带金额。"""
    from src.pipeline import _claim_fields
    recs = [{"item_code": "REVENUE", "period": "2024", "value": "170899152276.34"},
            {"item_code": "REVENUE", "period": "2024", "value": "170899152276.34"},
            {"item_code": "NET_PROFIT", "period": "2024", "value": "89334728025.90"}]
    fields = _claim_fields(recs)
    assert len(fields) == 2                      # 同 (科目,期间) 去重
    for f in fields:
        assert set(f) == {"item_code", "period"}
        assert "value" not in f


def test_claim_evidence_flags_unknown_claim_id():
    """模型返回不存在的 claim_id → 证据无效，不得进入核查。"""
    from src.pipeline import _attach_claim_evidence
    got = _attach_claim_evidence(
        [{"claim_id": "c01"}, {"claim_id": "不存在"}],
        {"c01": {"source": "rpt.docx", "section": "核心观点", "sentence": "…"}})
    assert got[0]["evidence"]["verified"] is True
    assert got[1]["evidence"]["verified"] is False


def test_multi_indicator_matches_are_kept():
    """一条 claim 可对应多个指标（同一 claim_id 多条 match），不得去重丢失。"""
    from src.pipeline import _attach_claim_evidence
    src = {"c01": {"source": "r.docx", "section": "核心观点", "sentence": "…"}}
    got = _attach_claim_evidence(
        [{"claim_id": "c01", "item_code": "TOTAL_REVENUE"},
         {"claim_id": "c01", "item_code": "NP_PARENT"}], src)
    assert len(got) == 2
    assert {g["item_code"] for g in got} == {"TOTAL_REVENUE", "NP_PARENT"}
    assert all(g["evidence"]["verified"] for g in got)


def test_gen_commentary_filters_to_core_metrics():
    """研报式评价只喂核心科目，非核心科目（如附注明细）不进入输入。"""
    from src.inputs import build_gen_commentary
    p = build_gen_commentary(
        [{"code": "TOTAL_REVENUE", "name": "营业总收入", "period": "2024",
          "value": "174140000000.00"},
         {"code": "SUPP_DEPRECIATION", "name": "补充资料固定资产折旧", "period": "2024",
          "value": "123.45"}],
        {"csi": 87.5, "grade": "A", "grade_label": "基本一致"},
        [], [], "基本一致")
    codes = {m["code"] for m in p["metrics"]}
    assert "TOTAL_REVENUE" in codes
    assert "SUPP_DEPRECIATION" not in codes


def test_commentary_whitelist_rejects_fabricated_number():
    """模型评价里出现程序没给过的数字，必须被数字白名单校验拦下。"""
    from src.inputs import build_gen_commentary, collect_allowed_numbers
    from src.validators import check_no_amounts
    p = build_gen_commentary(
        [{"code": "TOTAL_REVENUE", "name": "营业总收入", "period": "2024",
          "value": "174140000000.00"}],
        {"csi": 87.5, "grade": "A", "grade_label": "基本一致"}, [], [], "基本一致")
    allowed = collect_allowed_numbers(p)
    # 引用了输入里没有的"毛利率 92.5%"这种编造数字 → 必须被拦
    errs = check_no_amounts("公司毛利率为 92.5%，超出预期。", allowed)
    assert any("未经程序确认" in e for e in errs)
    # 合法引用输入里的数字不被拦
    assert check_no_amounts("营业总收入 174140000000.00 元。", allowed) == []


def test_all_registered_tasks_have_input_builders():
    """每个受注册任务必须各有输入构造器。"""
    from src import inputs
    from src.schemas import SCHEMA_BY_TASK
    for task in SCHEMA_BY_TASK:
        assert hasattr(inputs, f"build_{task}"), f"缺 build_{task}"


# ---------- B：派生计算（程序算数） ----------

_DERIVED_RECS = [
    {"item_code": "REVENUE", "period": "2024", "value": "1000000000"},
    {"item_code": "REVENUE", "period": "2023", "value": "800000000"},
    {"item_code": "OPERATING_COST", "period": "2024", "value": "400000000"},
    {"item_code": "OPERATING_COST", "period": "2023", "value": "300000000"},
    {"item_code": "TOTAL_REVENUE", "period": "2024", "value": "1000000000"},
    {"item_code": "TOTAL_REVENUE", "period": "2023", "value": "800000000"},
]


def test_derived_yoy_ratio_and_scale():
    """同比、比率、亿元换算必须是程序确定性算出的确切值（对齐研报的对比口径）。"""
    from src.derived import compute_derived
    got = {it["code"]: it for it in compute_derived(_DERIVED_RECS)}
    assert got["SCALE_TOTAL_REVENUE"]["value"] == "10.0"      # 10 亿元
    assert got["SCALE_TOTAL_REVENUE"]["unit"] == "亿元"
    assert got["YOY_TOTAL_REVENUE"]["value"] == "25.00"       # (1000-800)/800
    assert got["RATIO_GROSS"]["value"] == "60.0"              # (10-4)/10
    assert got["PCTCHG_RATIO_GROSS"]["unit"] == "pcts"


def test_derived_skips_missing_inputs():
    """数据不足的派生项必须缺席，而不是猜一个数出来。"""
    from src.derived import compute_derived
    got = {it["code"] for it in compute_derived(
        [{"item_code": "TOTAL_REVENUE", "period": "2024", "value": "1000000000"}])}
    assert "SCALE_TOTAL_REVENUE" in got        # 本期够，能换算亿元
    assert "YOY_TOTAL_REVENUE" not in got      # 没有上期，不得给同比
    assert "RATIO_GROSS" not in got            # 没有营业成本，不得给毛利率


def test_derived_zero_denominator_skips_without_crash():
    """分母为零的派生项必须直接缺席、且不得抛除零异常；分子为零则是合法的。

    六项比率的**分母**逐项守零：毛利率分母=营业收入、净利率系分母=营业总收入、
    经营现金流净利润比分母=净利润、资产负债率分母=资产总计（`not in (None, 0)`）。
    注意分子为零不得跳过：营业成本为 0 且收入非 0 时，毛利率 100% 是正确结果。
    """
    from src.derived import compute_derived
    # ① 分母为零（营业收入/营业总收入/净利润/资产总计 均为 0）→ 六项比率一律缺席
    got = {it["code"] for it in compute_derived([
        {"item_code": "REVENUE", "period": "2024", "value": "0"},
        {"item_code": "OPERATING_COST", "period": "2024", "value": "50000000"},
        {"item_code": "TOTAL_REVENUE", "period": "2024", "value": "0"},
        {"item_code": "NET_PROFIT", "period": "2024", "value": "0"},
        {"item_code": "CF_OPERATING", "period": "2024", "value": "2000000"},
        {"item_code": "TOTAL_ASSETS", "period": "2024-12-31", "value": "0"},
        {"item_code": "TOTAL_LIAB", "period": "2024-12-31", "value": "1000000"},
    ])}
    for code in ("RATIO_GROSS", "RATIO_NET", "RATIO_NPP", "RATIO_OP",
                 "RATIO_CASH", "RATIO_LEV"):
        assert code not in got, f"零分母仍产出了 {code}"
    # ② 分子为零是合法的：营业成本为 0、收入非 0 → 毛利率 100%（不得被跳过）
    got2 = {it["code"]: it for it in compute_derived([
        {"item_code": "REVENUE", "period": "2024", "value": "1000000000"},
        {"item_code": "OPERATING_COST", "period": "2024", "value": "0"},
    ])}
    assert got2["RATIO_GROSS"]["value"] == "100.0"


def test_derived_basis_and_name_contain_no_digits():
    """口径说明/名称/单位不得含阿拉伯数字——否则公式里的常数会污染数字白名单。"""
    import re
    from src.derived import compute_derived
    for it in compute_derived(_DERIVED_RECS):
        for field in ("name", "unit", "basis"):
            assert not re.search(r"\d", it[field]), f"{it['code']} 的 {field} 混入了数字"


def test_commentary_payload_carries_derived_into_whitelist():
    """派生值随 payload 进入数字白名单：模型可引用同比/毛利率，但仍不能编造新数。"""
    from src.derived import compute_derived
    from src.inputs import build_gen_commentary, collect_allowed_numbers
    from src.validators import check_no_amounts
    derived = compute_derived(_DERIVED_RECS)
    p = build_gen_commentary(
        [{"code": "TOTAL_REVENUE", "name": "营业总收入", "period": "2024",
          "value": "1000000000"}],
        {"csi": 95, "grade": "A", "grade_label": "高度一致"}, [], [], "高度一致", derived)
    assert p["counts"]["derived"] == len(p["derived"]) == len(derived)
    allowed = collect_allowed_numbers(p)
    # 引用程序派生出的同比与毛利率 → 放行
    assert check_no_amounts("营业总收入 10.0 亿元，同比 25.00%，毛利率 60.0%。", allowed) == []
    # 程序给负数时，正文写成"同比下降12.50%"（负号在词后、不在数字里）同样放行
    assert check_no_amounts("归母净利润同比下降 12.50%。", {"-12.50"}) == []
    assert check_no_amounts("同比 -12.50%", {"-12.50"}) == []
    # 但程序只给了正数时，把符号写反必须被拦下
    assert any("未经程序确认" in e
               for e in check_no_amounts("同比 -25.00%", allowed))
    # 程序没给过的数字 → 仍必须被拦
    assert any("未经程序确认" in e
               for e in check_no_amounts("毛利率高达 88.8%。", allowed))


# ---------- B2：确定性财务分析模块（src/financial_analysis.py） ----------

_FA_RECS = [
    {"item_code": "REVENUE", "period": "2024", "value": "1000"},
    {"item_code": "REVENUE", "period": "2023", "value": "800"},
    {"item_code": "OPERATING_COST", "period": "2024", "value": "400"},
    {"item_code": "OPERATING_COST", "period": "2023", "value": "300"},
    {"item_code": "TOTAL_REVENUE", "period": "2024", "value": "1000"},
    {"item_code": "TOTAL_REVENUE", "period": "2023", "value": "800"},
    {"item_code": "NET_PROFIT", "period": "2024", "value": "200"},
    {"item_code": "NP_PARENT", "period": "2024", "value": "180"},
    {"item_code": "TOTAL_PROFIT", "period": "2024", "value": "250"},
    {"item_code": "FIN_EXPENSE", "period": "2024", "value": "10"},
    {"item_code": "INCOME_TAX", "period": "2024", "value": "50"},
    {"item_code": "TAX_SURCHARGE", "period": "2024", "value": "20"},
    {"item_code": "SELLING_EXPENSE", "period": "2024", "value": "30"},
    {"item_code": "ADMIN_EXPENSE", "period": "2024", "value": "40"},
    {"item_code": "SHORT_TERM_LOAN", "period": "2024-12-31", "value": "100"},
    {"item_code": "LONG_TERM_LOAN", "period": "2024-12-31", "value": "200"},
    {"item_code": "TOTAL_EQUITY", "period": "2024-12-31", "value": "500"},
    {"item_code": "EQUITY_PARENT", "period": "2024-12-31", "value": "500"},
    {"item_code": "REVENUE_LIQUOR", "period": "2024", "value": "900"},
    {"item_code": "REVENUE_MOUTAI", "period": "2024", "value": "700"},
    {"item_code": "REVENUE_SERIES", "period": "2024", "value": "200"},
    {"item_code": "REVENUE_DIRECT", "period": "2024", "value": "600"},
    {"item_code": "REVENUE_WHOLESALE", "period": "2024", "value": "300"},
    {"item_code": "REVENUE_IMOUTAI", "period": "2024", "value": "400"},
    {"item_code": "VOLUME_MOUTAI", "period": "2024", "value": "10"},
    {"item_code": "VOLUME_SERIES", "period": "2024", "value": "20"},
]


def test_financial_analysis_profitability_and_expense():
    """盈利能力（毛利率/净利率/ROE/ROIC）与费用率必须是程序算出的确切值。"""
    from src.financial_analysis import analyze
    got = {m["code"]: m for m in analyze(_FA_RECS)["metrics"]}
    assert got["RATIO_GROSS"]["value"] == "60.0"      # (1000-400)/1000
    assert got["RATIO_NET"]["value"] == "20.0"        # 200/1000
    assert got["ROE"]["value"] == "36.0"              # 180/500
    # ROIC: (250+10)*(1-50/250) / (100+200+500) = 208/800 = 26.0
    assert got["ROIC"]["value"] == "26.0"
    assert got["EXP_TAX_RATE"]["value"] == "2.0"      # 20/1000
    assert got["EXP_SELLING_RATE"]["value"] == "3.0"  # 30/1000
    assert got["EXP_ADMIN_RATE"]["value"] == "4.0"    # 40/1000


def test_financial_analysis_trend_and_structure():
    """趋势（同比/pcts）与结构（分产品/分渠道占比、吨价）的确定性数值。"""
    from src.financial_analysis import analyze
    got = {m["code"]: m for m in analyze(_FA_RECS)["metrics"]}
    assert got["YOY_TOTAL_REVENUE"]["value"] == "25.00"       # (1000-800)/800
    assert got["PCTCHG_RATIO_GROSS"]["unit"] == "pcts"
    assert got["PCTCHG_RATIO_GROSS"]["value"] == "-2.5"       # 60.0 - 62.5
    assert got["MIX_PRODUCT_MOUTAI"]["value"] == "77.8"       # 700/900
    assert got["MIX_CHANNEL_IMOUTAI"]["value"] == "44.4"      # 400/900
    assert got["TON_PRICE_MOUTAI"]["value"] == "70.00"        # 700/10
    assert got["TON_PRICE_MOUTAI"]["unit"] == "元/吨"


def test_financial_analysis_missing_inputs_warn_not_crash():
    """输入缺失时给出明确提示而非崩溃；非法输入也不得抛异常。"""
    from src.financial_analysis import analyze
    # 空输入
    empty = analyze([])
    assert empty["metrics"] == [] and empty["warnings"]
    # 非法类型
    assert analyze(None)["metrics"] == []
    assert analyze("not-a-list")["metrics"] == []
    # 只有营业收入、没有成本 → 不产出毛利率，并给出提示
    one = analyze([{"item_code": "REVENUE", "period": "2024", "value": "1000"}])
    codes = {m["code"] for m in one["metrics"]}
    assert "RATIO_GROSS" not in codes
    assert any("毛利率" in w for w in one["warnings"])


def test_financial_analysis_roic_skips_when_equity_missing_without_crash():
    """ROIC 分支在 TOTAL_EQUITY 缺失时不得触发 TypeError。

    `te is not None and (sl + ll + te) != 0` 靠 `and` 短路：te 为 None 时右侧加法
    不会被求值，故不会因 `None + Decimal` 抛 TypeError；该情形应只记 warning、不产出 ROIC。
    """
    from src.financial_analysis import analyze
    base = [{"item_code": "TOTAL_PROFIT", "period": "2024", "value": "100"},
            {"item_code": "FIN_EXPENSE", "period": "2024", "value": "10"},
            {"item_code": "INCOME_TAX", "period": "2024", "value": "25"},
            {"item_code": "SHORT_TERM_LOAN", "period": "2024-12-31", "value": "5"},
            {"item_code": "TOTAL_ASSETS", "period": "2024-12-31", "value": "1000"}]
    res = analyze(base)                      # 无 TOTAL_EQUITY，求值必须走到结尾而不抛异常
    assert "ROIC" not in {m["code"] for m in res["metrics"]}
    assert any("投入资本回报率" in w for w in res["warnings"])
    # 所有者权益存在但「短期借款＋长期借款＋所有者权益」合计为零 → 同样不产出、不崩溃
    zero = [{"item_code": "TOTAL_PROFIT", "period": "2024", "value": "100"},
            {"item_code": "FIN_EXPENSE", "period": "2024", "value": "10"},
            {"item_code": "INCOME_TAX", "period": "2024", "value": "25"},
            {"item_code": "SHORT_TERM_LOAN", "period": "2024-12-31", "value": "-100"},
            {"item_code": "LONG_TERM_LOAN", "period": "2024-12-31", "value": "100"},
            {"item_code": "TOTAL_EQUITY", "period": "2024-12-31", "value": "0"}]
    res0 = analyze(zero)
    assert "ROIC" not in {m["code"] for m in res0["metrics"]}
    assert any("投入资本回报率" in w for w in res0["warnings"])


def test_financial_analysis_names_basis_have_no_digits():
    """名称/单位/口径不得含阿拉伯数字——否则会污染数字白名单。"""
    import re
    from src.financial_analysis import analyze
    for m in analyze(_FA_RECS)["metrics"]:
        for field in ("name", "unit", "basis"):
            assert not re.search(r"\d", m[field]), f"{m['code']} 的 {field} 混入了数字"


def test_forecast_products_and_pe_scenarios():
    """盈利预测基础：分产品收入预测与 PE 情景测算必须是确定性值，且名称/口径不含数字。"""
    import re
    from src.forecast import forecast
    recs = [
        {"item_code": "REVENUE", "period": "2024", "value": "100000000000"},
        {"item_code": "REVENUE", "period": "2023", "value": "80000000000"},
        {"item_code": "REVENUE_MOUTAI", "period": "2024", "value": "70000000000"},
        {"item_code": "REVENUE_MOUTAI", "period": "2023", "value": "50000000000"},
        {"item_code": "REVENUE_SERIES", "period": "2024", "value": "20000000000"},
        {"item_code": "REVENUE_SERIES", "period": "2023", "value": "20000000000"},
        {"item_code": "NP_PARENT", "period": "2024", "value": "18000000000"},
    ]
    metrics = forecast(recs)["metrics"]
    got = {m["code"]: m for m in metrics}
    assert got["PROD_FORE_REVENUE_MOUTAI"]["value"] == "980.0"   # 700×(1+40%)
    assert got["PROD_FORE_REVENUE_SERIES"]["value"] == "200.0"   # 200×(1+0%)
    assert got["PE_MCAP_15"]["value"] == "2700.0"                # 180×15
    assert got["PE_MCAP_30"]["value"] == "5400.0"                # 180×30
    for m in metrics:
        for field in ("name", "unit", "basis"):
            assert not re.search(r"\d", m[field]), f"{m['code']} 的 {field} 混入了数字"
    # 输入不足（空/非法）不得抛异常
    assert forecast([])["metrics"] == [] and forecast(None)["metrics"] == []


def test_report_includes_financial_ratio_analysis_section():
    """报告新增独立「财务比率分析」章节，并按报表分类展示字段（PDF / HTML 均含）。"""
    from src.pipeline import ROOT
    from src.report import build_all

    base = ROOT / "runs" / "pytest_report_analysis" / "ingested"
    base.mkdir(parents=True, exist_ok=True)
    (base / "sample.json").write_text(json.dumps([
        {"item_code": "REVENUE", "item_raw": "营业收入", "period": "2024", "statement": "IS",
         "period_type": "FY", "value": "100000000000"},
        {"item_code": "REVENUE", "item_raw": "营业收入", "period": "2023", "statement": "IS",
         "period_type": "FY", "value": "80000000000"},
        {"item_code": "OPERATING_COST", "item_raw": "营业成本", "period": "2024",
         "statement": "IS", "period_type": "FY", "value": "40000000000"},
        {"item_code": "TOTAL_ASSETS", "item_raw": "资产总计", "period": "2024-12-31",
         "statement": "BS", "period_type": "BAL_END", "value": "1000000000000"},
    ], ensure_ascii=False), encoding="utf-8")

    made = build_all("pytest_report_analysis", ("html", "pdf", "xlsx"))
    html = (ROOT / made["html"]).read_text(encoding="utf-8")
    assert "财务比率分析" in html and "毛利率" in html
    assert "数据提取结果（按报表分类）" in html

    with __import__("pdfplumber").open(ROOT / made["pdf"]) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert "财务比率分析" in text and "毛利率" in text and "资产负债表" in text


def test_derived_half_year_period_is_timeseries_not_point():
    """半年报的 `2026-H1` 是时期数（含连字符≠时点）；按连字符判定会让同比/比率全失效。"""
    from src.derived import compute_derived
    got = {it["code"]: it for it in compute_derived([
        {"item_code": "TOTAL_REVENUE", "period": "2026-H1", "value": "1000000000"},
        {"item_code": "TOTAL_REVENUE", "period": "2025-H1", "value": "800000000"},
    ])}
    assert got["YOY_TOTAL_REVENUE"]["value"] == "25.00"


def test_commentary_sections_split():
    """模型输出的「【小节名】正文」要能拆成标题+正文，供三种报告分子标题渲染。"""
    from src.report import _commentary_sections
    secs = _commentary_sections("【核心观点】经营稳健。\n【经营情况】营业总收入…\n续写一句。")
    assert [s[0] for s in secs] == ["核心观点", "经营情况"]
    assert secs[0][1] == "经营稳健。"
    assert secs[1][1] == "营业总收入…续写一句。"


def test_commentary_section_names_whitelisted():
    """gen_commentary 小节名须落在五小节集合内，且必含【核心观点】。"""
    from src.validators import check_commentary_sections
    ok = ("【核心观点】经营稳健。\n【收入分析】收入…\n【盈利能力】毛利…\n"
          "【现金流】…\n【风险提示】…")
    assert check_commentary_sections(ok) == []
    errs = check_commentary_sections("【核心观点】…\n【经营情况】已弃用小节")
    assert errs and "未定义的小节名" in errs[0]
    missing = check_commentary_sections("【收入分析】…\n【盈利能力】…")
    assert missing and "核心观点" in missing[0]
    assert check_commentary_sections("")                     # 空文本同样缺核心观点


def test_gen_commentary_prompt_declares_five_sections():
    """提示词必须声明五小节、【核心观点】排首位，样例行也须含核心观点。"""
    from src.tasks import load_prompt
    text, _ = load_prompt("gen_commentary")
    idx = [text.index(s) for s in ("【核心观点】", "【收入分析】", "【盈利能力】",
                                   "【现金流】", "【风险提示】")]
    assert idx == sorted(idx)
    assert "5 个固定小节" in text
    sample = [ln for ln in text.splitlines() if ln.startswith('{"ok":true')]
    assert sample and sample[0].index("【核心观点】") < sample[0].index("【收入分析】")


def test_post_checks_require_core_viewpoint():
    """校验层：缺【核心观点】的评价被拦下（触发修复重试），五节齐全则放行。"""
    from src.tasks import _post_checks
    ctx = {"allowed_numbers": set()}
    ok = {"ok": True, "text": "【核心观点】…\n【收入分析】…\n【盈利能力】…\n"
                              "【现金流】…\n【风险提示】…"}
    assert _post_checks(ok, "gen_commentary", ctx) == []
    bad = {"ok": True, "text": "【收入分析】…\n【盈利能力】…"}
    errs = _post_checks(bad, "gen_commentary", ctx)
    assert any("核心观点" in e for e in errs)


def test_calls_text_is_readable_not_raw_json():
    """调用统计须展开为可读文本，不得把 JSON 花括号直接抛给评审。"""
    from src.report import _calls_text
    txt = _calls_text({"api_ok": 1, "cache_hit": 2, "cache_miss": 3,
                       "failed": 0, "total": 6})
    assert "{" not in txt and "}" not in txt
    assert "在线成功 1 次" in txt and "命中本地缓存 2 次" in txt
    assert "未命中缓存 3 次" in txt
    assert "调用失败" not in txt                     # failed 为 0 时不出现该句
    assert _calls_text({}) == "无模型调用记录。"


def test_excel_row_height_scales_with_text_length():
    """Excel 成段文本行高应按长度增长，避免打开时只显示第一行。"""
    from src.report import _text_row_height
    assert _text_row_height("", 70) == 15.0
    short = _text_row_height("一句话", 70)
    long_ = _text_row_height("一句话" * 100, 70)
    assert long_ > short >= 15.0


# ---------- C：缓存与失败分流 ----------

def test_cache_key_stable_and_sensitive():
    """同输入同键；提示词或输入数据变一个字节即不同键。"""
    from src.llm import cache_key
    k = cache_key("prompt", {"a": 1})
    assert cache_key("prompt", {"a": 1}) == k
    assert cache_key("prompt ", {"a": 1}) != k
    assert cache_key("prompt", {"a": 2}) != k


def test_cache_key_covers_all_inference_params(monkeypatch):
    """推理参数必须参与缓存键，否则改了参数却命中旧结果（见交付文档 model_adapter_contract.md §12）。"""
    import src.llm as L
    base = L.cache_key("p", {"a": 1})
    monkeypatch.setattr(L, "THINKING", {"type": "enabled"})
    assert L.cache_key("p", {"a": 1}) != base, "thinking 未参与缓存键"
    monkeypatch.setattr(L, "THINKING", {"type": "disabled"})
    monkeypatch.setattr(L, "PARAMS", {**L.PARAMS, "max_tokens": 123})
    assert L.cache_key("p", {"a": 1}) != base, "max_tokens 未参与缓存键"


def test_offline_cache_hit_without_network():
    """离线模式命中缓存应返回 CACHE_HIT，且不读取凭据、不联网。"""
    import src.llm as L
    run_id, prompt, payload = "pytest_cache_hit", "只输出 json 的提示词", {"task": "x", "n": 1}
    key = L.cache_key(prompt, payload)
    cache = L.ROOT / "runs" / run_id / "llm_cache" / f"{key}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"ok": True}, ensure_ascii=False), encoding="utf-8")
    data, status = L.call(prompt, payload, run_id=run_id, offline=True)
    assert status == "CACHE_HIT"
    assert data == {"ok": True}


def test_empty_content_is_not_retried(monkeypatch):
    """推理烧光额度导致正文为空时只调用一次——重试同一输入只会白烧额度。"""
    import src.llm as L
    from types import SimpleNamespace
    calls = {"n": 0}

    def fake_create(**kw):
        calls["n"] += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="length",
                                     message=SimpleNamespace(content=""))],
            usage=SimpleNamespace(completion_tokens=8000))

    fake_client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=fake_create)))
    monkeypatch.setattr(L, "_client", lambda *_a, **_k: fake_client)

    data, status = L.call("提示词 json", {"task": "x", "n": 2}, run_id="pytest_empty")
    assert data is None and status == "OUTPUT_EMPTY"
    assert calls["n"] == 1, "空正文应直接返回，不得重试"


def test_api_key_override_reaches_client(monkeypatch, tmp_path):
    """网页表单里临时填入的 Key 必须一路透传到出站客户端，且不进缓存键。"""
    import src.llm as L
    from types import SimpleNamespace
    seen = []
    # 隔离缓存目录：否则第一次调用会把缓存写进 runs/，重复运行变成 CACHE_HIT、断言不再成立
    monkeypatch.setattr(L, "ROOT", tmp_path)

    def fake_client(api_key=None):
        seen.append(api_key)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kw: SimpleNamespace(
                choices=[SimpleNamespace(finish_reason="stop",
                                         message=SimpleNamespace(content='{"ok": true}'))],
                usage=SimpleNamespace(completion_tokens=1)))))

    monkeypatch.setattr(L, "_client", fake_client)
    data, status = L.call("提示词 json", {"task": "k"}, run_id="pytest_key",
                          api_key="sk-ui-supplied")

    assert data == {"ok": True} and status.startswith("API_OK")
    assert seen and seen[0] == "sk-ui-supplied", "表单填的 Key 应传给出站客户端"
    # 缓存键不含凭据：同一输入恒得同一指纹（换 Key 不会让缓存失效）
    assert L.cache_key("提示词 json", {"task": "k"}) == L.cache_key("提示词 json", {"task": "k"})


def test_call_task_retry_keeps_api_key(monkeypatch):
    """校验失败后的修复重试也必须带上网页填的临时 Key——否则重试会静默回落 .env。"""
    import src.tasks as T
    seen = []

    def fake_call(prompt, payload, **kw):
        seen.append(kw.get("api_key"))
        if len(seen) == 1:                      # 第一次返回越界枚举，触发修复重试
            return {"items": [{"raw_header": "某科目",
                               "canonical_item": "NOT_A_CODE", "confidence": 0.9}]}, "API_OK(try=1)"
        return {"items": []}, "API_OK(try=1)"

    monkeypatch.setattr(T, "call", fake_call)
    out, status = T.call_task("match_item",
                              {"headers": ["某科目"], "standard_items": []},
                              "pytest_retry_key", api_key="sk-ui-supplied")

    assert len(seen) == 2, "越界枚举应触发一次修复重试"
    assert all(s == "sk-ui-supplied" for s in seen), "重试调用也不得丢掉表单填的 Key"
    assert out == {"ok": True, "items": [], "reason": ""} and "SCHEMA_FIXED" in status


def test_offline_miss_does_not_split_exponentially(monkeypatch):
    """缓存未命中时不得触发指数级拆分——拆得再小也不会变好。"""
    import src.pipeline as P
    calls = {"n": 0}

    def fake_call_task(*a, **k):
        calls["n"] += 1
        return None, "NO_CACHE_OFFLINE"

    monkeypatch.setattr(P, "call_task", fake_call_task)
    claims = [{"claim_id": f"c{i}", "sentence": "x"} for i in range(20)]
    matches, status = P._match_claim_batched(claims, [], "pytest_run", True)
    assert matches == []
    assert calls["n"] == 1, f"应只调用一次，实际 {calls['n']} 次"

# ---------- D：研报引用数值比对（C研 维度）----------

_ZH = {"TOTAL_REVENUE": "营业总收入", "REVENUE": "营业收入", "NP_PARENT": "归属母公司股东的净利润",
       "REVENUE_MOUTAI": "茅台酒营业收入", "REVENUE_SERIES": "系列酒营业收入"}


def test_figure_binds_to_nearest_preceding_name():
    """金额归属它前面最近的指标名，而不是"任意前置名字之后第一个金额"。"""
    from src.research_check import associate_figures
    s = "2024年公司实现营业总收入 1741.4 亿元/同比+15.66%，归母净利润 862.3 亿元/同比+15.38%。"
    got = associate_figures(s, _ZH)
    assert got["TOTAL_REVENUE"]["value"] == 174140000000
    assert got["NP_PARENT"]["value"] == 86230000000


def test_segment_revenue_not_attributed_to_total():
    """分部营收不得挂到总营收上——否则会用分部数去比总营收，必然误报。"""
    from src.research_check import associate_figures
    got = associate_figures("茅台酒 2024 年营收 1495.3 亿元/+15.28%", _ZH)
    assert "REVENUE" not in got, "『茅台酒…营收』的金额被误挂到营业收入"
    assert got["REVENUE_MOUTAI"]["value"] == 149530000000


def test_non_amount_units_are_ignored():
    """吨/股等非金额单位不得被当成钱。"""
    from src.research_check import associate_figures
    got = associate_figures("茅台酒销量 4.6 万吨/+10.22%", _ZH)
    assert got.get("REVENUE_MOUTAI") is None


def test_malformed_unit_is_flagged():
    """"亿万元"这类畸形单位必须判为单位错误，不能因为数值凑巧接近就放行。"""
    from src.research_check import associate_figures
    got = associate_figures("归母净利润 862.3 亿万元/同比+15.38%", _ZH)
    assert got["NP_PARENT"]["bad_unit"] == "亿万元"


def test_research_check_flags_unit_error_against_real_value():
    """用真实财报值验证：862.3亿万元的单位错误必须判 FAIL/UNIT_SCALE。

    该句由研报内检测器 RC04（单位口径）判定；RC01 也会得出同因（UNIT_SCALE），按
    (claim_id, cause) 去重后只留检测器那条（带页码/表行位置），故结果恰好 1 条。
    """
    from src.research_check import check
    matches = [{"claim_id": "c1", "item_code": "NP_PARENT", "period": "2024",
                "evidence": {"verified": True}}]
    claims = {"c1": {"claim_id": "c1",
                     "sentence": "归母净利润 862.3 亿万元/同比+15.38%"}}
    records = [{"item_code": "NP_PARENT", "period": "2024", "period_type": "FY",
                "value": "86228146421.62", "page": 64, "chunk_id": "p064_t1"}]
    out = check(matches, claims, records)
    assert len(out["results"]) == 1
    r = out["results"][0]
    assert r["status"] == "FAIL" and r["cause"] == "UNIT_SCALE"
    assert r["dimension"] == "C研"


def test_research_check_quarter_vs_annual_is_review_not_fail():
    """单季数与年度累计不可比：判 REVIEW，不得判 FAIL（误报控制）。"""
    from src.research_check import check
    matches = [{"claim_id": "c1", "item_code": "TOTAL_REVENUE", "period": "2024",
                "evidence": {}}]
    claims = {"c1": {"sentence": "其中 2024Q4 实现营业总收入 510.2 亿元/同比+12.77%"}}
    records = [{"item_code": "TOTAL_REVENUE", "period": "2024", "period_type": "FY",
                "value": "174144069958.25", "page": 63, "chunk_id": "p063_t1"}]
    out = check(matches, claims, records)
    assert out["results"] == []
    # 单季 vs 年度累计 → 「口径不可比」单列桶（不计入待复核计数）
    assert out["incomparable"][0]["status"] == "REVIEW"
    assert out["incomparable"][0]["reason_type"] == "PERIOD_MISMATCH"
    assert out["review"] == []


def test_research_check_passes_within_tolerance():
    """年报引用误差在 0.5% 内应判 PASS。"""
    from src.research_check import check
    matches = [{"claim_id": "c1", "item_code": "TOTAL_REVENUE", "period": "2024",
                "evidence": {}}]
    claims = {"c1": {"sentence": "2024年公司实现营业总收入 1741.4 亿元"}}
    records = [{"item_code": "TOTAL_REVENUE", "period": "2024", "period_type": "FY",
                "value": "174144069958.25", "page": 63, "chunk_id": "p063_t1"}]
    out = check(matches, claims, records)
    assert out["results"][0]["status"] == "PASS"


def test_research_check_forecast_table_row_is_review_not_fail():
    """预测/多列表格行不得硬判，转 REVIEW——避免张冠李戴的误报。

    回归背景（2025 批实测）：「毛利率 | 92.1% | 91.3% | …」的 92.1% 实为 2026E、
    「资产负债率 | 22.0% | …」的 22.0% 实为预测期，却被贴到 2025/2023/2024，
    凭空产出 4 条「异常」（标准答案并未植错）。
    """
    from src.research_check import check
    matches = [{"claim_id": "c1", "item_code": "RATIO_GROSS", "period": "2025",
                "evidence": {}}]
    claims = {"c1": {"sentence": "毛利率 | 92.1% | 91.3% | 90.6% | 90.4% | 90.6%"}}
    records = [{"item_code": "RATIO_GROSS", "period": "2025", "period_type": "FY",
                "value": "91.2"}]
    out = check(matches, claims, records)
    assert out["results"] == []
    # 预测/多列表格行 → 「预测值（无财报对照）」单列桶（不计入待复核计数）
    assert out["prediction"][0]["status"] == "REVIEW"
    assert out["prediction"][0]["reason_type"] == "PREDICTION_NO_ANNUAL_REF"
    assert out["review"] == []
    assert "预测" in out["prediction"][0]["reason"]


def test_research_check_accepts_parent_net_margin_as_same_item():
    """研报口语的"净利率"写成归母口径时按口径差异处理，不得判为引用错误。

    回归背景（2025 批）：研报 47.8% 是归母净利率（程序算得 47.8），而主口径
    净利润÷营业总收入 = 49.6，此前被判 FAIL——属假阳性。
    """
    from src.research_check import check
    records = [
        {"item_code": "RATIO_NET", "period": "2025", "period_type": "FY", "value": "49.6"},
        {"item_code": "NET_PROFIT", "period": "2025", "period_type": "FY",
         "value": "85278542558.43"},
        {"item_code": "TOTAL_REVENUE", "period": "2025", "period_type": "FY",
         "value": "172054171890.91"},
        {"item_code": "NP_PARENT", "period": "2025", "period_type": "FY",
         "value": "82320067101.68"},
    ]
    matches = [{"claim_id": "c1", "item_code": "RATIO_NET", "period": "2025",
                "evidence": {}}]
    claims = {"c1": {"sentence": "2025 年净利率同比-1.67pcts至 47.8%，截至年末合同负债环比增加。"}}
    out = check(matches, claims, records)
    r = out["results"][0]
    assert r["status"] == "PASS" and "口径差异" in r["reason"]


def test_margin_alt_truth_returns_none_when_not_applicable():
    """非净利率科目或缺数据时不产出备选口径真值——不猜、不补。"""
    from src.research_check import _index_records, margin_alt_truth
    idx = _index_records([{"item_code": "RATIO_GROSS", "period": "2025", "value": "91.2"}])
    assert margin_alt_truth("RATIO_GROSS", "2025", idx) is None
    assert margin_alt_truth("RATIO_NET", "2025", idx) is None


def test_research_check_flags_period_phrase_mismatch_as_fail():
    """期间短语写错、金额却取自正确期间：判 FAIL/PERIOD_MISMATCH。

    回归背景（2025 批·标准答案错误2）：研报把"截至 2025年末合同负债 80.1 亿元"
    改成"截至 2026年6月末"，金额没动，纯数值比对永远抓不到——此前的实现正是把它
    判成了 PASS（漏报）。
    """
    from src.research_check import check
    matches = [{"claim_id": "c1", "item_code": "CONTRACT_LIAB",
                "period": "2025-12-31", "evidence": {}}]
    claims = {"c1": {"sentence": "3）预收款看，截至 2026年6月末合同负债80.1 亿元/"
                                 "环比 Q3 末增加 2.6 亿元"}}
    records = [{"item_code": "CONTRACT_LIAB", "period": "2025-12-31",
                "period_type": "BAL_END", "value": "8006739780.94"}]
    out = check(matches, claims, records)
    r = out["results"][0]
    assert r["status"] == "FAIL" and r["cause"] == "PERIOD_MISMATCH"
    assert "2026" in r["reason"] and "80.1亿元" in r["left"]["raw"]


def test_research_check_period_mismatch_with_wrong_amount_is_review():
    """期间与金额同时对不上：不作断言，转人工复核（避免把匹配错误记成研报的错）。"""
    from src.research_check import check
    matches = [{"claim_id": "c1", "item_code": "CONTRACT_LIAB",
                "period": "2025-12-31", "evidence": {}}]
    claims = {"c1": {"sentence": "截至 2026年6月末合同负债 120 亿元"}}
    records = [{"item_code": "CONTRACT_LIAB", "period": "2025-12-31",
                "period_type": "BAL_END", "value": "8006739780.94"}]
    out = check(matches, claims, records)
    assert out["results"] == []
    # 期间与金额同时对不上 → 「口径不可比」单列桶（不计入待复核计数）
    assert out["incomparable"][0]["status"] == "REVIEW"
    assert out["incomparable"][0]["reason_type"] == "PERIOD_MISMATCH"
    assert "期间" in out["incomparable"][0]["reason"]


def test_research_check_flags_yoy_sign_error_as_rc02():
    """数值之后的"同比±X%"方向写反：产出 RC02 的 FAIL/SIGN_ERROR。

    回归背景（2025 批·标准答案错误4）：真值 -1.2% 被写成 +1.2%。
    """
    from src.research_check import check
    matches = [{"claim_id": "c1", "item_code": "TOTAL_REVENUE", "period": "2025",
                "evidence": {}}]
    claims = {"c1": {"sentence": "2025 年公司实现营业总收入1720.5 亿元/同比+1.2%；"}}
    records = [
        {"item_code": "TOTAL_REVENUE", "period": "2025", "period_type": "FY",
         "value": "172054171890.91"},
        {"item_code": "TOTAL_REVENUE", "period": "2024", "period_type": "FY",
         "value": "174144069958.25"},
    ]
    out = check(matches, claims, records)
    rc01 = [r for r in out["results"] if r["rule_id"].startswith("RC01")]
    rc02 = [r for r in out["results"] if r["rule_id"].startswith("RC02")]
    assert rc01[0]["status"] == "PASS"          # 金额本身对得上，错误只出在同比方向
    assert rc02[0]["status"] == "FAIL" and rc02[0]["cause"] == "SIGN_ERROR"
    assert rc02[0]["left"]["raw"].endswith("+1.2%")


def test_research_check_yoy_rounding_is_not_flagged():
    """研报同比只保留一位小数的舍入差异（真值 -1.20 vs 研报 -1.2）不得报错。"""
    from src.research_check import check
    matches = [{"claim_id": "c1", "item_code": "TOTAL_REVENUE", "period": "2025",
                "evidence": {}}]
    claims = {"c1": {"sentence": "2025 年公司实现营业总收入1720.5 亿元/同比-1.2%；"}}
    records = [
        {"item_code": "TOTAL_REVENUE", "period": "2025", "period_type": "FY",
         "value": "172054171890.91"},
        {"item_code": "TOTAL_REVENUE", "period": "2024", "period_type": "FY",
         "value": "174144069958.25"},
    ]
    out = check(matches, claims, records)
    assert all(not r["rule_id"].startswith("RC02") for r in out["results"])
    assert out["review"] == []


def test_research_check_yoy_not_applied_to_quarter_or_ratio():
    """单季句与比率类不做同比核验：基期不同（同期 vs 上一期 / 百分点 vs 百分比）。"""
    from src.research_check import check
    # ① 单季句：整体已因"单季 vs 年度累计不可比"转复核，不得再产出 RC02
    q_matches = [{"claim_id": "c1", "item_code": "TOTAL_REVENUE", "period": "2025",
                  "evidence": {}}]
    q_claims = {"c1": {"sentence": "其中 2025Q4 实现营业总收入 412 亿元/同比-19.3%"}}
    q_records = [
        {"item_code": "TOTAL_REVENUE", "period": "2025", "period_type": "FY",
         "value": "172054171890.91"},
        {"item_code": "TOTAL_REVENUE", "period": "2024", "period_type": "FY",
         "value": "174144069958.25"},
    ]
    out = check(q_matches, q_claims, q_records)
    assert out["results"] == []
    assert all(not r["rule_id"].startswith("RC02") for r in out["review"])
    # ② 比率类：毛利率的"同比"惯例以百分点表述，基数与百分比不同，不判
    r_matches = [{"claim_id": "c2", "item_code": "RATIO_GROSS", "period": "2025",
                  "evidence": {}}]
    r_claims = {"c2": {"sentence": "2025 年毛利率 91.2%/同比-0.6%"}}
    r_records = [
        {"item_code": "RATIO_GROSS", "period": "2025", "period_type": "FY", "value": "91.2"},
        {"item_code": "RATIO_GROSS", "period": "2024", "period_type": "FY", "value": "90.5"},
    ]
    out = check(r_matches, r_claims, r_records)
    assert [r["rule_id"] for r in out["results"]] == ["RC01@v1.0"]


def _row(sentence: str, cid: str, kind: str = "TABLE_ROW") -> dict:
    return {"claim_id": cid, "kind": kind, "source": "研报.docx",
            "section": "表1", "page": 1, "row_no": 2, "sentence": sentence}


def test_intra_detectors_flag_calc_error_rc03():
    """RC03 表内自校验：调整幅度列 ≠ (新值÷旧值−1) → FAIL/VALUE_ERROR。

    回归背景（2024 批·标准答案错误5）：营业收入 2026E 调整幅度真值 2.6% 被写成 1.6%。
    """
    from src.research_check import _run_detectors
    claims = [_row("营业收入（百万元） | 174,144 | 187,750 | 201,721 | "
                   "190,754 | 207,065 | 1.6% | 1.6%", "r2024_t02_02")]
    results, flagged = _run_detectors(claims)
    rc03 = [r for r in results if r["rule_id"].startswith("RC03")]
    assert rc03 and rc03[0]["status"] == "FAIL" and rc03[0]["cause"] == "VALUE_ERROR"
    assert "r2024_t02_02" in flagged


def test_intra_detectors_flag_calc_sign_error_rc03():
    """RC03：调整幅度符号与「(新值÷旧值−1)」相反 → FAIL/SIGN_ERROR。

    回归背景（2025 批·标准答案错误5）：归母净利润 2026E 调整幅度真值 −5.4% 被写成 +5.4%。
    """
    from src.research_check import _run_detectors
    claims = [_row("归母净利润（百万元） | 82,320 | 89,512 | 91,425 | "
                   "84,658 | 90,356 | +5.4% | -1.2%", "r2025_t02_06")]
    results, _ = _run_detectors(claims)
    rc03 = [r for r in results if r["rule_id"].startswith("RC03")]
    assert rc03 and rc03[0]["cause"] == "SIGN_ERROR"


def test_intra_detectors_flag_unit_inconsistency_rc04():
    """RC04：报表行标签单位与文档统一口径不一致 → FAIL/UNIT_SCALE。

    回归背景（2026H1 批·标准答案错误1）：其余行标「(百万元)」，净利润行被改成「(千万元)」。
    """
    from src.research_check import _run_detectors
    claims = [
        _row("营业收入(百万元) | 174,144 | 172,054", "t01_01", "TABLE_LABEL"),
        _row("净利润(千万元) | 86228 | 82320 | 82367", "t01_03", "TABLE_LABEL"),
        _row("归母净利润(百万元) | 82320 | 84658", "t01_05", "TABLE_LABEL"),
    ]
    results, flagged = _run_detectors(claims)
    rc04 = [r for r in results if r["rule_id"].startswith("RC04")]
    assert rc04 and rc04[0]["claim_id"] == "t01_03"
    assert rc04[0]["cause"] == "UNIT_SCALE" and "t01_03" in flagged


def test_intra_detectors_flag_forecast_range_start_rc05():
    """RC05：预测年份区间起点 ≤ 报告年度 → FAIL/PERIOD_MISMATCH。

    回归背景（2026H1 批·标准答案错误2）：真值「预计 2026-2028 年」被写成 2025-2027 年。
    """
    from src.research_check import _run_detectors
    claims = [{"claim_id": "r2026_p08_015", "kind": "TEXT", "source": "研报.docx",
               "section": "核心观点", "page": 1, "para_no": 5,
               "sentence": "我们预计 2025-2027 年公司实现营业总收入 1733/1838/1993 亿元。"}]
    results, flagged = _run_detectors(claims)
    rc05 = [r for r in results if r["rule_id"].startswith("RC05")]
    assert rc05 and rc05[0]["cause"] == "PERIOD_MISMATCH"
    assert "r2026_p08_015" in flagged


def test_intra_detectors_flag_sign_series_rc05():
    """RC05：正文「同比减少X%/Y%/Z%」与表行同列全正值 → FAIL/SIGN_ERROR。

    回归背景（2026H1 批·标准答案错误4）：真值「同比增长0.7%/6.0%/8.5%」被写成「减少」。
    """
    from src.research_check import _run_detectors
    claims = [
        {"claim_id": "p08_015", "kind": "TEXT", "source": "研报.docx",
         "section": "核心观点", "page": 1, "para_no": 5,
         "sentence": "预计 2026-2028 年公司实现营业总收入 1733/1838/1993 亿元，"
                     "同比减少0.7%/6.0%/8.5%；"},
        _row("(+/-%) | 15.7% | -1.2% | 0.7% | 6.0% | 8.5%", "t01_02"),
    ]
    results, _ = _run_detectors(claims)
    rc05 = [r for r in results if r["rule_id"].startswith("RC05")]
    assert any(r["cause"] == "SIGN_ERROR" for r in rc05)


def test_intra_detectors_flag_text_vs_row_value_rc05():
    """RC05：正文数值序列与全百分号表行同列「多数相同、个别不同」→ FAIL/VALUE_ERROR。

    回归背景（2026H1 批·标准答案错误5）：净利润增长率 2026E 真值 0.1% 被写成 0.2%。
    """
    from src.research_check import _run_detectors
    claims = [
        {"claim_id": "p_x", "kind": "TEXT", "source": "研报.docx",
         "section": "核心观点", "page": 1,
         "sentence": "净利润增长0.1%/7.6%/9.7%，增速平稳。"},
        _row("净利润增长率 | 15.4% | -4.5% | 0.2% | 7.6% | 9.7%", "t05_33"),
    ]
    results, _ = _run_detectors(claims)
    rc05 = [r for r in results if r["rule_id"].startswith("RC05")]
    hit = [r for r in rc05 if r["cause"] == "VALUE_ERROR"]
    assert hit and hit[0]["claim_id"] == "t05_33"


def test_intra_detector_fail_is_not_duplicated_in_review():
    """检测器已判 FAIL 的 claim 不得再被 RC01 流程记成一条「待复核」。"""
    from src.research_check import check
    claims = {"c1": {"claim_id": "c1", "kind": "TEXT",
                     "sentence": "归母净利润 862.3 亿万元/同比+15.38%"}}
    matches = [{"claim_id": "c1", "item_code": "NP_PARENT", "period": "2024",
                "evidence": {}}]
    out = check(matches, claims, [])
    assert [r["status"] for r in out["results"]] == ["FAIL"]
    assert out["review"] == []


def test_one_claim_can_carry_two_causes():
    """同一句话里的两类错误都要报出来，不得按 claim 整体排他。

    回归背景（2025 批·标准答案错误1+错误4）：「营业总收入1721 千万元/同比+1.2%」
    既是单位错（应为亿元）、又是同比方向错（真值 -1.20%）。此前 claim 一旦被 RC04 判为
    单位错就整条不再走 RC01/RC02，同比方向错被整条丢掉——这正是"正负号漏报"的根因。
    """
    from src.research_check import check
    matches = [{"claim_id": "c1", "item_code": "TOTAL_REVENUE", "period": "2025",
                "evidence": {}}]
    claims = {"c1": {"claim_id": "c1", "kind": "TEXT", "page": 1, "para_no": 2,
                     "sentence": "2025 年公司实现营业总收入1721 千万元/同比+1.2%；"}}
    records = [
        {"item_code": "TOTAL_REVENUE", "period": "2025", "period_type": "FY",
         "value": "172054171890.91"},
        {"item_code": "TOTAL_REVENUE", "period": "2024", "period_type": "FY",
         "value": "174144069958.25"},
    ]
    out = check(matches, claims, records)
    fails = [r for r in out["results"] if r["status"] == "FAIL"]
    assert sorted(r["cause"] for r in fails) == ["SIGN_ERROR", "UNIT_SCALE"]
    rc02 = [r for r in fails if r["rule_id"].startswith("RC02")]
    assert "同比方向" in rc02[0]["reason"]                # 文案措辞可换，方向必须点名
    assert "正负号" in rc02[0]["reason"]
    assert rc02[0]["expected"] == "-1.2%"                 # 「应为（正确值）」给出真值
    assert rc02[0]["left"]["raw"] == "同比+1.2%"          # 原串不得带前导分隔符
    assert out["review"] == []                            # 已判异常的 claim 不再重复进复核


def test_research_check_multi_value_enumeration_is_review_not_fail():
    """多值枚举（847/904/982 亿元）不得拿末位去比本期间——判 REVIEW，不判 FAIL。

    回归背景（2025 批）：`预计归母净利润847/904/982 亿元` 里 NUM_RE 只截到末位
    「982 亿元」（实为 2028E），却被当成 2025 年值去比财报 823.2 亿元，凭空产出一条
    "研报值 982 亿 vs 财报 823 亿"的误报（标准答案并未植错）。
    """
    from src.research_check import check
    matches = [{"claim_id": "c1", "item_code": "NP_PARENT", "period": "2025",
                "evidence": {}}]
    claims = {"c1": {"claim_id": "c1", "kind": "TEXT",
                     "sentence": "我们预计 2025 年归母净利润847/904/982 亿元，"
                                 "同比+2.8%/+6.7%/+8.6%（前值 26/27 年 895/914 亿元）。"}}
    records = [{"item_code": "NP_PARENT", "period": "2025", "period_type": "FY",
                "value": "82320067101.68"}]
    out = check(matches, claims, records)
    assert out["results"] == []
    assert out["review"][0]["status"] == "REVIEW"
    assert "多值枚举" in out["review"][0]["reason"]


def test_research_check_yoy_without_prior_year_is_review():
    """研报标了同比、财报侧却没有上一年度数：必须显式留痕，不得静默跳过。

    回归背景：只上传单份年报时，同比既无真值可比也无任何提示——研报把方向写反
    在报告上完全看不见，这正是"正负号漏报"的结构性温床。
    """
    from src.research_check import check
    matches = [{"claim_id": "c1", "item_code": "TOTAL_REVENUE", "period": "2025",
                "evidence": {}}]
    claims = {"c1": {"claim_id": "c1", "kind": "TEXT",
                     "sentence": "2025 年公司实现营业总收入1720.5 亿元/同比+1.2%；"}}
    records = [{"item_code": "TOTAL_REVENUE", "period": "2025", "period_type": "FY",
                "value": "172054171890.91"}]
    out = check(matches, claims, records)
    assert [r["rule_id"] for r in out["results"]] == ["RC01@v1.0"]   # 金额本身对得上
    # 缺上期基期 → 「预测值（无财报对照）」单列桶（本系统只用年报、不引外部预期，无基线可比）
    assert out["prediction"][0]["rule_id"].startswith("RC02")
    assert out["prediction"][0]["reason_type"] == "NO_BASELINE"
    assert "上一期" in out["prediction"][0]["reason"]


def test_research_check_amount_followed_by_yoy_is_not_enumeration():
    """「金额/+X%」里的斜杠是分隔符而非枚举分隔，不得被判成多值枚举。

    回归背景：曾把「茅台酒2025 年收入1465 亿元/+0.39%」判成枚举转复核，连带把真异常
    「系列酒 2025 年收入 232亿元」也降级掉了——金额的枚举只能看它**前面**是否为裸数字+斜杠。
    """
    from src.research_check import associate_figures
    got = associate_figures("1）茅台酒2025 年收入1465 亿元/+0.39%，"
                            "2）系列酒 2025 年收入 232亿元/-9.8% ；", _ZH)
    assert got["REVENUE_MOUTAI"]["enumerated"] is False
    assert got["REVENUE_SERIES"]["enumerated"] is False
    # 真枚举：末位带单位的那一个，其前面是「裸数字 + /」
    got2 = associate_figures("预计归母净利润847/904/982 亿元", _ZH)
    assert got2["NP_PARENT"]["enumerated"] is True


def test_yoy_truth_rejects_non_fy_period():
    """同比基准只认纯年份期间：时点与半年度的上期基数不同，一律不产出。"""
    from decimal import Decimal
    from src.research_check import _fy_index, yoy_truth
    fy = _fy_index([
        {"item_code": "TOTAL_REVENUE", "period": "2025", "value": "100"},
        {"item_code": "TOTAL_REVENUE", "period": "2024", "value": "80"},
        {"item_code": "TOTAL_REVENUE", "period": "2025-12-31", "value": "999"},
        {"item_code": "TOTAL_REVENUE", "period": "2025-H1", "value": "50"},
    ])
    assert yoy_truth("TOTAL_REVENUE", "2025", fy) == Decimal("25.00")
    assert yoy_truth("TOTAL_REVENUE", "2025-12-31", fy) is None
    assert yoy_truth("TOTAL_REVENUE", "2025-H1", fy) is None
    assert yoy_truth("TOTAL_REVENUE", "2024", fy) is None      # 缺 2023 基期，不猜


def test_period_year_before_takes_nearest_preceding_year():
    """期间修饰取指标名**之前最近**的年份，不得取句中更早的年份。"""
    from src.research_check import associate_figures, period_year_before
    sent = "2024 年营收下滑，2025 年营业总收入1720.5 亿元"
    fig = associate_figures(sent, _ZH)["TOTAL_REVENUE"]
    assert period_year_before(sent, fig) == "2025"


def test_dedup_claim_matches_demotes_multi_period_without_year():
    """同一指标被枚举多个期间且句中无年份 → 整组转人工复核，不留互相矛盾的多条结果。"""
    from src.research_check import dedup_claim_matches
    matches = [
        {"claim_id": "c1", "item_code": "RATIO_LEV", "period": p,
         "evidence": {"sentence": "资产负债率 | 22.0% | 19.5% | 19.3% | 19.1%"}}
        for p in ("2023-12-31", "2024-12-31", "2025-12-31")
    ]
    kept, demoted = dedup_claim_matches(matches)
    assert kept == [] and len(demoted) == 1
    assert demoted[0]["item_code"] == "RATIO_LEV" and "期间无法对齐" in demoted[0]["reason"]


def test_dedup_claim_matches_keeps_other_items_and_year_matched():
    """句中有对应年份时保留；同句其他指标的匹配不得被连带丢弃。"""
    from src.research_check import dedup_claim_matches
    sent = "2025 年营业总收入 1720.5 亿元，归母净利润 823.2 亿元"
    matches = [
        {"claim_id": "c1", "item_code": "TOTAL_REVENUE", "period": "2025",
         "evidence": {"sentence": sent}},
        {"claim_id": "c1", "item_code": "NP_PARENT", "period": "2025",
         "evidence": {"sentence": sent}},
        {"claim_id": "c1", "item_code": "TOTAL_REVENUE", "period": "2024",
         "evidence": {"sentence": sent}},
    ]
    kept, demoted = dedup_claim_matches(matches)
    assert demoted == []
    assert sorted((m["item_code"], m["period"]) for m in kept) == [
        ("NP_PARENT", "2025"), ("TOTAL_REVENUE", "2025")]


# ---------- E：文件夹入口 ----------

def test_year_from_name():
    """报告期从文件名识别；识别不到才用 --year 兜底。"""
    from pathlib import Path
    from src.ingest import year_from_name
    assert year_from_name(Path("2024年年度报告.docx")) == 2024
    assert year_from_name(Path("贵州茅台【2025全年】模拟研究报告.docx")) == 2025
    assert year_from_name(Path("无年份.docx"), fallback=2023) == 2023


def test_classify_by_content_not_filename(monkeypatch):
    """按文件内容分类，不看文件名——各人写法不一，靠名字会误判。"""
    from pathlib import Path
    from src import ingest
    monkeypatch.setattr(ingest, "read_text",
                        lambda p: "合并资产负债表 合并利润表 合并现金流量表")
    assert ingest.classify(Path("随便叫什么.docx"))[0] == "annual"
    monkeypatch.setattr(ingest, "read_text",
                        lambda p: "核心观点 投资评级 盈利预测 风险提示")
    assert ingest.classify(Path("随便叫什么.docx"))[0] == "research"
    monkeypatch.setattr(ingest, "read_text", lambda p: "一段无关的话")
    assert ingest.classify(Path("随便叫什么.docx"))[0] == "unknown"


def test_classify_accepts_audit_report_wording(monkeypatch):
    """年度审计报告正文写"合并及母公司资产负债表/利润表/现金流量表"，也必须识别出来。

    这份材料是财报类文件（附审计意见），此前用字面量"合并资产负债表"匹配不到它，
    整份被判"未识别"，报告里给出的理由是"既不像年报也不像研报"——与事实不符。
    识别为年报材料后，即便这份审计报告正文不含报表数字、抽不到字段，也会走
    "未抽到任何核心字段"这条正常的跳过路径，理由与文件性质一致。
    """
    from pathlib import Path
    from src import ingest
    monkeypatch.setattr(
        ingest, "read_text",
        lambda p: "我们审计了贵州茅台公司财务报表，包括 2025 年 12 月 31 日的合并及母公司"
                  "资产负债表，2025 年度的合并及母公司利润表、合并及母公司现金流量表，"
                  "以及相关财务报表附注。")
    assert ingest.classify(Path("随便叫什么.docx"))[0] == "annual"


def test_load_structured_ignores_non_field_records():
    """目录里混入研报 claims（也是 JSON 数组）时不得被当成财报字段记录。"""
    from src.pipeline import ROOT, load_structured
    d = ROOT / "runs" / "pytest_struct"
    d.mkdir(parents=True, exist_ok=True)
    (d / "chunk_index.json").write_text('{"p001_t1": {"page": 1}}', encoding="utf-8")
    (d / "fields.json").write_text(json.dumps(
        [{"item_code": "REVENUE", "period": "2024", "value": "100.00"}],
        ensure_ascii=False), encoding="utf-8")
    (d / "claims.json").write_text(json.dumps(
        [{"claim_id": "c1", "sentence": "营业收入 100 亿元"}], ensure_ascii=False),
        encoding="utf-8")
    got = load_structured("runs/pytest_struct")
    assert len(got) == 1 and got[0]["item_code"] == "REVENUE"


def test_claims_carry_source_hash():
    """研报 claim 必须带内容哈希：文件名没变但内容被改时要能发现。"""
    from src.ingest import ROOT
    p = ROOT / "data" / "demo" / "研报claims_2024.json"
    if not p.exists():
        return                      # 基线缺失时跳过，不误报
    claims = json.loads(p.read_text(encoding="utf-8"))
    assert claims and all(len(c.get("source_hash", "")) == 64 for c in claims)


def test_malformed_unit_does_not_crash():
    """畸形单位（亿万元/千万元）必须判单位错误，且不得因量级字查表抛 KeyError。"""
    from src.research_check import associate_figures, check
    zh = {"TOTAL_REVENUE": "营业总收入", "NP_PARENT": "归属母公司股东的净利润"}
    for unit in ("亿万元", "千万元"):
        got = associate_figures(f"归母净利润 862.3 {unit}", zh)
        assert got["NP_PARENT"]["bad_unit"] == unit, unit

    # 端到端：不得抛异常，且判 FAIL/UNIT_SCALE
    matches = [{"claim_id": "c1", "item_code": "TOTAL_REVENUE", "period": "2025",
                "evidence": {}}]
    out = check(matches, {"c1": {"sentence": "营业总收入 1721 千万元"}},
                [{"item_code": "TOTAL_REVENUE", "period": "2025", "period_type": "FY",
                  "value": "172054171890.91", "page": 61, "chunk_id": "p061_t1"}])
    assert out["results"][0]["status"] == "FAIL"
    assert out["results"][0]["cause"] == "UNIT_SCALE"
    # 说明必须**指名道姓**该改成哪种单位，并给出终值——只写"千万元不是认可的单位"
    # 使用者无法判断该改成亿元还是百万元（实测的反馈即是这一点看不懂）。
    r0 = out["results"][0]
    assert "亿元" in r0["reason"] and "1721亿元" in r0["reason"]
    assert r0["expected"] == "1721亿元"


def test_research_check_survives_bad_sentence(monkeypatch):
    """单条解析异常只丢该条转人工复核，不得打断整体流程。"""
    from src import research_check as RC
    monkeypatch.setattr(RC, "associate_figures",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    out = RC.check(
        [{"claim_id": "c1", "item_code": "REVENUE", "period": "2024", "evidence": {}}],
        {"c1": {"sentence": "营业收入 100 亿元"}},
        [{"item_code": "REVENUE", "period": "2024", "period_type": "FY",
          "value": "10000000000.00", "page": 63, "chunk_id": "p063_t1"}])
    assert out["results"] == []
    assert out["review"] and "解析异常" in out["review"][0]["reason"]


# ---------- F：规则引擎（只凭财报就出结果）----------

def test_safe_eval_only_arithmetic():
    """规则表达式求值必须拒绝任意代码，只放行 Decimal 四则运算。"""
    from decimal import Decimal
    from src.rules_engine import ExpressionError, safe_eval
    env = {"A": Decimal("10"), "B": Decimal("4")}
    assert safe_eval("A - (B + 1)", env) == Decimal("5")
    assert safe_eval("-A", env) == Decimal("-10")
    for bad in ("__import__('os')", "A.__class__", "A if A else B"):
        try:
            safe_eval(bad, env)
            raise AssertionError(f"应当拒绝：{bad}")
        except (ExpressionError, SyntaxError):
            pass


def test_rules_engine_balance_sheet_identity():
    """资产=负债+权益 成立时判 PASS，人为改动后判 FAIL。"""
    from src.rules_engine import run_rules
    base = [
        {"item_code": "TOTAL_ASSETS", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "1000.00", "unit_raw": "元", "raw_text": "资产总计 1000.00"},
        {"item_code": "TOTAL_LIAB", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "400.00", "unit_raw": "元", "raw_text": "负债合计 400.00"},
        {"item_code": "TOTAL_EQUITY", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "600.00", "unit_raw": "元", "raw_text": "所有者权益合计 600.00"},
    ]
    out = run_rules(base, "pytest_rules_ok")
    bs01 = [r for r in out["results"] if r["rule_id"].startswith("BS01")]
    assert bs01 and bs01[0]["status"] == "PASS"

    broken = [dict(r) for r in base]
    broken[0]["value"] = "1234.00"          # 人为破坏平衡
    out2 = run_rules(broken, "pytest_rules_bad")
    bs01b = [r for r in out2["results"] if r["rule_id"].startswith("BS01")]
    assert bs01b[0]["status"] == "FAIL"
    assert bs01b[0]["diff"] == "234.00"


def test_rules_engine_missing_fields_are_na_not_fail():
    """字段缺失判 NA（不计入分母），不得判 FAIL——否则是误报。"""
    from src.rules_engine import run_rules
    out = run_rules([
        {"item_code": "CASH_END", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "100.00", "unit_raw": "元", "raw_text": "货币资金 100.00"},
    ], "pytest_rules_na")
    nb = [r for r in out["results"] if r["rule_id"].startswith("NB01")]
    assert nb and all(r["status"] == "NA" for r in nb)


def test_cross_period_rule_detects_mismatch():
    """同一时点的期末数 vs 下一年报的期初数不等时应判 FAIL。"""
    from src.rules_engine import run_rules
    out = run_rules([
        {"item_code": "CASH_END", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "100.00", "unit_raw": "元", "raw_text": "货币资金 100.00"},
        {"item_code": "CASH_END", "period": "2024-12-31", "period_type": "BAL_BEG",
         "value": "999.00", "unit_raw": "元", "raw_text": "货币资金 999.00"},
    ], "pytest_cross")
    xq = [r for r in out["results"] if r["rule_id"].startswith("XQ_CASH")]
    assert xq and xq[0]["status"] == "FAIL"


def test_zero_anomaly_run_does_not_crash():
    """一条 FAIL/REVIEW 都没有时不落 explanations.json，流程不得因此崩掉。

    此前 pipeline 无条件读该文件，导致"数据干净、零异常"的运行在最后一步报
    FileNotFoundError——越干净的输入越走不通。
    """
    import json as _json
    from src.pipeline import ROOT, explain_summary
    run_id = "pytest_zero_anomaly"
    base = ROOT / "runs" / run_id / "rules"
    base.mkdir(parents=True, exist_ok=True)
    (base / "explanations.json").unlink(missing_ok=True)
    assert explain_summary(run_id) == "无异常，无需解释"

    (base / "explanations.json").write_text(
        _json.dumps({"explained": 3}, ensure_ascii=False), encoding="utf-8")
    assert explain_summary(run_id) == "3 条"


def test_csi_normalizes_and_grades():
    """CSI 只对有可执行规则的维度归一化；全不可执行时输出 null 而非 0。"""
    from src.rules_engine import compute_csi
    cfg = {"version": "1.0", "dimensions": {"C内": {"weight": 0.25}, "C研": {"weight": 0.10}}}
    ok = compute_csi([{"dimension": "C内", "status": "PASS"}], cfg)
    assert ok["csi"] == 100.0 and ok["grade"] == "A"
    assert ok["weights_normalized"]["C内"] == 1.0        # 只剩一个维度 → 权重归一到 1
    empty = compute_csi([], cfg)
    assert empty["csi"] is None and empty["grade"] == "NA"


def test_cross_period_rule_accepts_distinct_end_and_beg_items():
    """期末/期初可以是两个不同编号（权益变动表把两者拆开），也必须能对上。"""
    from src.rules_engine import run_rules
    out = run_rules([
        {"item_code": "SI_UNDIST_END", "period": "2023-12-31", "period_type": "BAL_END",
         "value": "500.00", "unit_raw": "元", "raw_text": "四、本期期末余额 …"},
        {"item_code": "SI_UNDIST_BEG", "period": "2023-12-31", "period_type": "BAL_BEG",
         "value": "500.00", "unit_raw": "元", "raw_text": "二、本年期初余额 …"},
    ], "pytest_cross_beg")
    r = [x for x in out["results"] if x["rule_id"].startswith("XQ_UNDIST")]
    assert r and r[0]["status"] == "PASS", "期初/期末拆成两个编号后跨期规则失效"


def test_report_year_rule_aligns_end_beg_and_period_amounts():
    """报告年度对齐：期末(2024-12-31)、期初(2023-12-31)、时期数(2024) 合到同一期再算。

    这三种期间按 (period, period_type) 分组会落进三个组、每组都缺字段而误判 NA。
    """
    from src.rules_engine import run_rules
    recs = [
        {"item_code": "SI_UNDIST_END", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "1000.00", "unit_raw": "元", "raw_text": "四、本期期末余额 …"},
        {"item_code": "SI_UNDIST_BEG", "period": "2023-12-31", "period_type": "BAL_BEG",
         "value": "800.00", "unit_raw": "元", "raw_text": "二、本年期初余额 …"},
        {"item_code": "SI_NP_PARENT", "period": "2024", "period_type": "FY",
         "value": "300.00", "unit_raw": "元", "raw_text": "（一）综合收益总额 …"},
        {"item_code": "SI_SURPLUS_APPROP", "period": "2024", "period_type": "FY",
         "value": "-50.00", "unit_raw": "元", "raw_text": "1．提取盈余公积 …"},
        {"item_code": "SI_DIVIDEND", "period": "2024", "period_type": "FY",
         "value": "-50.00", "unit_raw": "元", "raw_text": "3．对所有者（或股东）的分配 …"},
    ]
    out = run_rules(recs, "pytest_report_year")
    si = [r for r in out["results"] if r["rule_id"].startswith("SI01")]
    assert si and si[0]["status"] == "PASS", "报告年度对齐未生效"
    assert si[0]["period"] == "2024"

    broken = [dict(r) for r in recs]
    broken[0]["value"] = "1234.00"
    out2 = run_rules(broken, "pytest_report_year_bad")
    si2 = [r for r in out2["results"] if r["rule_id"].startswith("SI01")]
    assert si2[0]["status"] == "FAIL" and si2[0]["diff"] == "234.00"


def test_zero_if_absent_only_defaults_the_listed_fields():
    """固定模板表的空白行按 0 计（zero_if_absent），锚点字段缺失仍判 NA。

    调节项若被一律按 0 计（取到值也不用），等式会退化成"差额=全部调节项之和"。
    """
    from src.rules_engine import run_rules
    recs = [
        {"item_code": "SUPP_CF_OPERATING", "period": "2024", "period_type": "FY",
         "value": "1300.00", "unit_raw": "元", "raw_text": "经营活动产生的现金流量净额 …"},
        {"item_code": "SUPP_NET_PROFIT", "period": "2024", "period_type": "FY",
         "value": "1000.00", "unit_raw": "元", "raw_text": "净利润 …"},
        # 有取到值的调节项必须用真实值（此处 300 = 1300 − 1000）
        {"item_code": "SUPP_DEPRECIATION", "period": "2024", "period_type": "FY",
         "value": "300.00", "unit_raw": "元", "raw_text": "固定资产折旧 …"},
    ]
    out = run_rules(recs, "pytest_zero_if_absent")
    cf = [r for r in out["results"] if r["rule_id"].startswith("CF02")]
    assert cf and cf[0]["status"] == "PASS", f"调节项被当成 0：{cf[0].get('fields')}"
    assert cf[0]["fields"]["SUPP_DEPRECIATION"] == "300.00"

    # 锚点字段缺失 → NA，不得因为"其余项都能按 0 计"而算成通过
    out2 = run_rules([r for r in recs if r["item_code"] != "SUPP_NET_PROFIT"],
                     "pytest_zero_if_absent_na")
    cf2 = [r for r in out2["results"] if r["rule_id"].startswith("CF02")]
    assert cf2 and all(r["status"] == "NA" for r in cf2)


def test_rule_library_size_and_field_references():
    """规则库 20–30 条（《项目思路》要求），且每条引用的字段都在字典里。"""
    import yaml
    from src.pipeline import ROOT
    fields = yaml.safe_load((ROOT / "rules" / "fields.yaml").read_text(encoding="utf-8"))
    cfg = yaml.safe_load((ROOT / "rules" / "rules.yaml").read_text(encoding="utf-8"))
    codes = set(fields["fields"])
    rules = cfg["rules"]
    assert 20 <= len(rules) <= 30, f"规则条数 {len(rules)} 不在 20–30"
    for rule in rules:
        for key in ("requires", "zero_if_absent"):
            for c in rule.get(key, []):
                assert c in codes, f"{rule['rule_id']} 引用未定义字段 {c}"
        for key in ("item", "item_beg"):
            if rule.get(key):
                assert rule[key] in codes
    # 五个维度都要有规则，否则 CSI 会缺维度
    dims = {r["dimension"] for r in rules}
    assert {"C内", "C间", "C期", "C附"} <= dims


# ---------- F2：分部数据 / 所有者权益变动表 / 附注明细的抽取 ----------

def test_segment_table_attribution_by_section():
    """分部表按小节归属：同名行在不同小节含义不同，不切小节就会串数。"""
    from tools.extract_fields import _scan_segment_table
    rows = [
        ["主营业务分行业情况"],
        ["分行业", "营业收入", "营业成本", "毛利率（%）"],
        ["酒类", "1000.00", "400.00", "60.00"],
        ["主营业务分产品情况"],
        ["分产品", "营业收入", "营业成本", "毛利率（%）"],
        ["茅台酒", "700.00", "250.00", "64.00"],
        ["其他系列酒", "300.00", "150.00", "50.00"],
    ]
    parsed = _scan_segment_table(rows)
    got = {(r[0], r[1]) for r in parsed}
    assert ("REVENUE_LIQUOR", "1000.00") in got
    assert ("REVENUE_MOUTAI", "700.00") in got
    assert ("REVENUE_SERIES", "300.00") in got
    assert ("COST_SERIES", "150.00") in got
    # item_raw 是原文行名，且必须出现在 raw_text 里（否则证据校验判未通过）
    for code, value, raw_text, _no, label in parsed:
        assert label in raw_text and label in ("酒类", "茅台酒", "其他系列酒")
    # 列头没确认"营业收入在第 1 列"时整表不取，不能按猜的列号取数
    bad = [["主营业务分产品情况"], ["分产品", "营业成本", "营业收入"], ["茅台酒", "250.00", "700.00"]]
    assert _scan_segment_table(bad) == []


def test_segment_heading_matches_split_half_year_titles():
    """半年报 PDF 常把四个小节标题分行写；标题正则须命中任一分部小节，否则整段漏抽。"""
    from tools.extract_fields import SEGMENT_HEADING_RE
    # 分行写法：单独的"主营业务分行业情况"也要能唤醒分部提取
    assert SEGMENT_HEADING_RE.search("主营业务分行业情况")
    assert SEGMENT_HEADING_RE.search("主营业务分产品情况")
    assert SEGMENT_HEADING_RE.search("主营业务分销售模式情况")
    # 年报的合并写法仍要命中（不因放宽而漏）
    assert SEGMENT_HEADING_RE.search("主营业务分行业、分产品、分地区、分销售模式情况")
    # 无关文本不得误命中
    assert not SEGMENT_HEADING_RE.search("主营业务收入情况")


def test_segment_sales_mode_imoutai_and_direct():
    """分销售模式小节：i茅台与直销各自归到自己的 item_code，不并成一条。"""
    from tools.extract_fields import _scan_segment_table
    rows = [
        ["主营业务分销售模式情况"],
        ["销售模式", "营业收入", "营业成本"],
        ["直销", "1000.00", "300.00"],
        ["i茅台", "600.00", "180.00"],
        ["批发代理", "1700.00", "500.00"],
    ]
    got = {(r[0], r[1]) for r in _scan_segment_table(rows)}
    assert ("REVENUE_DIRECT", "1000.00") in got
    assert ("REVENUE_IMOUTAI", "600.00") in got
    assert ("REVENUE_WHOLESALE", "1700.00") in got


def test_volume_table_picks_sales_column_not_yoy():
    """产销量表取"销售量"列数值；"销售量比上年增减"是同比列，不得被当成销量。"""
    from tools.extract_fields import _scan_volume_table
    rows = [
        ["主要产品", "单位", "生产量", "销售量", "库存量",
         "生产量比上年增减", "销售量比上年增减"],
        ["茅台酒", "吨", "57,000.00", "46,310.45", "10,000.00", "5.00", "3.00"],
        ["其他系列酒", "吨", "58,000.00", "43,000.12", "8,000.00", "6.00", "4.00"],
    ]
    got = {(r[0], r[1]) for r in _scan_volume_table(rows)}
    # 销量是第 3 列（46,310.45），不是同比列（3.00）
    assert ("VOLUME_MOUTAI", "46,310.45") in got
    assert ("VOLUME_SERIES", "43,000.12") in got
    assert all(v != "3.00" for _c, v, _t, _n, _l in _scan_volume_table(rows))
    # 表头没有"销售量"列 → 整表不取，不能把生产量/库存量当销量
    assert _scan_volume_table([["主要产品", "单位", "生产量", "库存量"],
                              ["茅台酒", "吨", "57,000.00", "10,000.00"]]) == []


def test_equity_table_parsing_carries_year_to_continuation():
    """权益变动表在 Word 里常被拆成"表主体 + 续表"，续表没有年份，必须沿用上一张的。"""
    from tools.extract_fields import _scan_equity_table
    head = ["项目", "2024 年度"]
    title_row = ["", "归属于母公司所有者权益", "少数股东权益", "所有者权益合计"]
    cells = [""] * 16
    cells[0], cells[9], cells[11] = "四、本期期末余额", "50.00", "900.00"
    cells[13], cells[14], cells[15] = "950.00", "30.00", "980.00"
    main = [head, title_row, cells]
    cont = [["6．其他"] + [""] * 15, cells]

    rows, year = _scan_equity_table(main, None)
    assert year == 2024
    got = {r[0]: r for r in rows}
    assert got["SI_UNDIST_END"][1] == "900.00" and got["SI_UNDIST_END"][2] == "2024-12-31"
    assert got["SI_EQ_TOTAL_END"][1] == "980.00"
    # item_raw 必须是原文行名，且真的出现在 raw_text 里（证据包含性校验要用）
    assert got["SI_UNDIST_END"][6] == "四、本期期末余额"
    assert got["SI_UNDIST_END"][6] in got["SI_UNDIST_END"][4]

    # 续表：没有年份表头 → 沿用 2024；列数不符 → 整表不取（宁可不取也不取错）
    rows2, year2 = _scan_equity_table(cont, year)
    assert year2 == 2024 and {r[0] for r in rows2} == set(got)
    rows3, _ = _scan_equity_table([head, ["四、本期期末余额"] + [""] * 5], None)
    assert rows3 == []


def test_equity_row_period_types_are_not_mixed():
    """期末/期初是时点数，综合收益与利润分配是时期数——期间不许混（§1.5）。"""
    from tools.extract_fields import _scan_equity_table
    head = ["项目", "2024 年度"]

    def row(label, undist="10.00"):
        c = [""] * 16
        c[0], c[11] = label, undist
        return c

    rows, _ = _scan_equity_table(
        [head, row("二、本年期初余额"), row("四、本期期末余额"),
         row("（一）综合收益总额"), row("1．提取盈余公积"), row("3．对所有者（或股东）的分配")], None)
    by_code = {r[0]: (r[2], r[3]) for r in rows}
    assert by_code["SI_UNDIST_BEG"] == ("2023-12-31", "BAL_BEG")
    assert by_code["SI_UNDIST_END"] == ("2024-12-31", "BAL_END")
    assert by_code["SI_NP_PARENT"] == ("2024", "FY")
    assert by_code["SI_SURPLUS_APPROP"] == ("2024", "FY")
    assert by_code["SI_DIVIDEND"] == ("2024", "FY")


def test_cf_supplement_matcher_is_order_sensitive():
    """补充资料表用「包含」匹配且顺序敏感：短键排前面会把长键的行抢走。"""
    from tools.extract_fields import NOTE_TARGETS, _match_note_item
    spec = NOTE_TARGETS["现金流量表补充资料"]
    assert _match_note_item(spec, "净利润") == "SUPP_NET_PROFIT"
    assert _match_note_item(spec, "经营活动产生的现金流量净额") == "SUPP_CF_OPERATING"
    assert _match_note_item(spec, "现金及现金等价物净增加额") == "SUPP_CF_NET_INCREASE"
    # 2024 写"折耗"、2025 写"折旧"，同一行两种写法都要认
    assert _match_note_item(spec, "固定资产折旧、油气资产折耗、生产性生物资产折旧") == "SUPP_DEPRECIATION"
    assert _match_note_item(spec, "固定资产折旧、油气资产折旧、生产性生物资产折旧") == "SUPP_DEPRECIATION"
    assert _match_note_item(spec, "处置固定资产、无形资产和其他长期资产的损失（收益以“ － ”号填列）") == "SUPP_DISPOSAL_LOSS"
    assert _match_note_item(spec, "固定资产报废损失（收益以“ - ”号填列）") == "SUPP_SCRAP_LOSS"
    # 小节标题行也含"净利润"，但它以冒号结尾、不是数据行，必须挡掉
    assert _match_note_item(spec, "1.将净利润调节为经营活动现金流量：") is None
    assert _match_note_item(spec, "2.不涉及现金收支的重大投资和筹资活动：") is None


def test_note_headings_distinguish_section_and_subsection():
    """一级小节 "5、 应收账款" 与子小节 "（2）按坏账计提方法分类披露" 必须分开认。

    混为一谈的话，note 会被子小节标题覆盖，货币资金小节之后的表全被错抽。
    """
    from tools.extract_fields import NOTE_HEADING_RE, NOTE_SUBHEADING_RE
    assert NOTE_HEADING_RE.match("5、 应收账款").group(1) == "应收账款"
    assert NOTE_HEADING_RE.match("8. 货币资金√适用").group(1) == "货币资金"
    assert NOTE_HEADING_RE.match("（1）现金流量表补充资料") is None
    assert NOTE_SUBHEADING_RE.match("（1）按账龄披露").group(1) == "按账龄披露"
    assert NOTE_SUBHEADING_RE.match("(2).按坏账计提方法分类披露").group(1) == "按坏账计提方法分类披露"


def test_ar_note_row_target_sums_to_net_receivable():
    """应收账款附注：净额 = 账面余额 − 坏账准备（规则表 7/26 条的口径）。"""
    from tools.extract_fields import _match_row_target, _squash
    got = _match_row_target("应收账款", "按坏账计提方法分类披露")
    assert got is not None, "应收账款坏账计提附注未配置"
    assert _match_row_target("其他应收款", "按坏账计提方法分类披露") is None, \
        "其他应收款附注结构相同，不能当成应收账款"
    assert _match_row_target("应收账款", "按账龄披露") is None
    row = ["合计", "19,995,290.75", "/", "1,021,098.00", "/", "18,974,192.75"]
    _, cur, _ = got
    from decimal import Decimal
    assert Decimal(row[cur["AR_GROSS"]].replace(",", "")) \
        - Decimal(row[cur["AR_BADDEBT"]].replace(",", "")) == Decimal("18974192.75")
    assert _squash("（一）综合收益总额") == "(一)综合收益总额"


def test_pdf_extracts_ar_note_target_and_skips_decoy(tmp_path):
    """P2-1：PDF 数据源也要能抽到应收账款"按坏账计提方法分类披露"的合计行。

    造一页最小 PDF，同页放两张结构完全相同的表（先"按账龄披露"诱饵、后目标表），
    诱饵金额刻意不同。断言：只认目标表（判据是表内出现"单项计提/组合计提"行），
    诱饵的 111.11/222.22 一个都不许漏进来——真值与诱饵值不同，可精确区分。
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from tools.extract_fields import _extract_pdf

    # ★ reportlab 默认 Helvetica 无法编码中文，直接建表会渲染成空白方框，
    #   pdfplumber 便读不出「应收账款」，附注识别必然失败。必须注册 CJK CID 字体，
    #   并在 Paragraph 与 Table 上都显式指定，才能造出可解析的中文测试 PDF。
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

    # 列头首格必须是表头名（如"项目"）而非"合计"：抽取逻辑按"第一格==合计"
    #   定位合计行，若列头首格也写成"合计"，会被当成合计行先命中而整表取不到数。
    header = ["项目", "账面余额", "比例", "坏账准备", "计提比例", "账面价值",
              "期初账面余额", "期初比例", "期初坏账准备", "期初计提比例", "期初账面价值"]
    decoy = [["账龄"] + header[1:],
             ["合计", "111.11", "100.00", "222.22", "5.11", "-111.11",
              "333.33", "100.00", "444.44", "2.91", "-111.11"]]
    target = [header,
              ["单项计提", "0.00", "0.00", "0.00", "0.00", "0.00",
               "0.00", "0.00", "0.00", "0.00", "0.00"],
              ["按组合计提", "19,995,290.75", "100.00", "1,021,098.00", "5.11", "18,974,192.75",
               "62,181,941.03", "100.00", "1,808,530.62", "2.91", "60,373,410.41"],
              ["合计", "19,995,290.75", "100.00", "1,021,098.00", "5.11", "18,974,192.75",
               "62,181,941.03", "100.00", "1,808,530.62", "2.91", "60,373,410.41"]]

    def mk(data):
        t = Table(data, colWidths=[22 * mm] * 11)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                               ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
                               ("FONTSIZE", (0, 0), (-1, -1), 7)]))
        return t

    styles = getSampleStyleSheet()
    cjk = ParagraphStyle("cjk", parent=styles["Normal"], fontName="STSong-Light")
    doc = SimpleDocTemplate(str(tmp_path / "ar_note.pdf"), pagesize=A4)
    doc.build([
        Paragraph("5、应收账款", cjk),
        Paragraph("（1）按账龄披露", cjk), mk(decoy), Spacer(1, 24),
        Paragraph("（2）按坏账计提方法分类披露", cjk), mk(target),
    ])

    recs, _ = _extract_pdf(tmp_path / "ar_note.pdf", 2024)
    got = {(r["item_code"], r["period"]): r["value"] for r in recs
           if r["item_code"] in ("AR_GROSS", "AR_BADDEBT")}
    assert got == {
        ("AR_GROSS", "2024-12-31"): "19995290.75",
        ("AR_BADDEBT", "2024-12-31"): "1021098.00",
        ("AR_GROSS", "2023-12-31"): "62181941.03",
        ("AR_BADDEBT", "2023-12-31"): "1808530.62",
    }, f"应收账款坏账计提附注未按预期抽取（诱饵值不得漏入）：{got}"
    from decimal import Decimal
    assert Decimal(got[("AR_GROSS", "2024-12-31")]) - Decimal(got[("AR_BADDEBT", "2024-12-31")]) \
        == Decimal("18974192.75")


# ---------- G：核查报告生成 ----------

def _make_run_fixture(run_id: str) -> None:
    """造一个自包含的运行产物目录。

    测试不得依赖仓库里既有的 runs/<某次运行>——那是本地产物，队友 clone 后不存在，
    断言会因为"没数据"而失败（而不是因为代码错）。这里显式造数据。
    """
    from src.pipeline import ROOT
    base = ROOT / "runs" / run_id
    (base / "rules").mkdir(parents=True, exist_ok=True)
    (base / "manifest.json").write_text(json.dumps(
        {"calls": {"api_ok": 1, "cache_hit": 0, "cache_miss": 0, "failed": 0, "total": 1},
         "model": {"model_id": "deepseek-flash"},
         "prompts": [{"prompt_id": "match_item", "sha256": "a" * 64}]},
        ensure_ascii=False), encoding="utf-8")
    (base / "rules" / "results.json").write_text(json.dumps({"results": [
        {"rule_id": "BS01@1.0", "dimension": "C内", "status": "FAIL", "period": "2024-12-31",
         "expr": "TOTAL_ASSETS - (TOTAL_LIAB + TOTAL_EQUITY)", "diff": "2000.00",
         "tolerance": "100.00", "severity": "HIGH",
         "evidence": {"file": "a.docx", "locator": "表#92/第45行", "raw_text": "资产总计 …"}},
        {"rule_id": "BS02@1.0", "dimension": "C内", "status": "PASS", "period": "2024-12-31"},
    ]}, ensure_ascii=False), encoding="utf-8")
    (base / "rules" / "csi.json").write_text(json.dumps({
        "csi": 87.5, "grade": "B", "grade_label": "基本一致",
        "weights_used": {"C内": 0.25}, "weights_normalized": {"C内": 1.0},
        "dimensions": {"C内": {"score": 0.875, "rules_applicable": 2, "pass": 1,
                               "fail": 1, "review": 0, "na": 0}},
        "na_dimensions": [], "note": ""}, ensure_ascii=False), encoding="utf-8")
    # 现场解析产物（有它才会出现「提取字段数」与总评里的字段计数）
    (base / "ingested").mkdir(parents=True, exist_ok=True)
    (base / "ingested" / "sample.json").write_text(json.dumps([
        {"item_code": "TOTAL_ASSETS", "item_raw": "资产总计", "period": "2024-12-31",
         "period_type": "BAL_END", "value": "1000", "page": 9, "raw_text": "资产总计 1000"},
        {"item_code": "TOTAL_LIAB", "item_raw": "负债合计", "period": "2024-12-31",
         "period_type": "BAL_END", "value": "400", "page": 9, "raw_text": "负债合计 400"},
    ], ensure_ascii=False), encoding="utf-8")
    # C研 维度比对结果：一条不一致 + 一条待复核，供「研报差异点评」渲染
    (base / "rules" / "research_claims.json").write_text(json.dumps({
        "results": [
            {"rule_id": "RC01@v1.0", "dimension": "C研", "claim_id": "c1", "status": "FAIL",
             "item_code": "TOTAL_REVENUE", "period": "2024",
             "left": {"source": "research_report", "raw": "1741.4 亿元", "value": "174140000000"},
             "right": {"item_code": "TOTAL_REVENUE", "period": "2024", "value": "170000000000"},
             "diff": "4140000000", "tolerance": "1000", "severity": "HIGH",
             "cause": "VALUE_ERROR", "reason": "研报值 / 财报值 = 1.0244，非 10 的整数次幂",
             "expected": "1741.4亿元",
             "evidence": {"file": "b.docx", "section": "核心观点",
                          "sentence": "公司2024年营业总收入1741.4亿元",
                          "verified": True}},
        ],
        "review": [{"claim_id": "c2", "reason": "模型未返回该条的匹配结果",
                    "sentence": "预计2025年收入增长8%"}],
    }, ensure_ascii=False), encoding="utf-8")


def test_report_build_all_three_formats():
    """三种报告都能生成，且都含免责声明（interfaces.md §6.3(3)）。"""
    from openpyxl import load_workbook
    from src.pipeline import ROOT
    from src.report import DISCLAIMER, build_all

    _make_run_fixture("pytest_report")
    made = build_all("pytest_report", ("html", "xlsx", "pdf"))
    assert set(made) == {"html", "xlsx", "pdf"}
    for rel in made.values():
        assert (ROOT / rel).stat().st_size > 0

    html = (ROOT / made["html"]).read_text(encoding="utf-8")
    assert DISCLAIMER in html and "CSI" in html

    wb = load_workbook(ROOT / made["xlsx"])
    assert {"摘要", "异常清单", "规则明细", "提取字段"} <= set(wb.sheetnames)
    assert any(DISCLAIMER in str(c.value) for row in wb["摘要"].iter_rows()
               for c in row if c.value)

    with __import__("pdfplumber").open(ROOT / made["pdf"]) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert DISCLAIMER in text and "CSI" in text


def test_report_includes_summary_and_research_review():
    """报告必须给出成段结论：核查总评 + 研报观点与财报数字差异点评（三种格式都有）。"""
    from openpyxl import load_workbook
    from src.pipeline import ROOT
    from src.report import build_all

    _make_run_fixture("pytest_report_review")
    made = build_all("pytest_report_review", ("html", "xlsx", "pdf"))

    html = (ROOT / made["html"]).read_text(encoding="utf-8")
    assert "核查总评" in html
    # 总评必须真的复述程序产物里的数字，而不是空话
    assert "提取结构化字段 2 条" in html
    assert "异常集中在 表内勾稽 1 条" in html
    assert "CSI = 87.5" in html
    assert "等级 B（基本一致）" in html
    assert "处置建议" in html
    # 研报差异点评必须带上研报原句与差异，便于人工追溯
    assert "研报观点与财报数字差异点评" in html
    assert "公司2024年营业总收入1741.4亿元" in html
    assert "TOTAL_REVENUE" in html

    wb = load_workbook(ROOT / made["xlsx"])
    summary_cells = [str(c.value) for row in wb["摘要"].iter_rows() for c in row if c.value]
    assert any(v.startswith("核查总评") for v in summary_cells)
    assert any("研报差异点评" == v for v in summary_cells)
    assert any("提取结构化字段 2 条" in v for v in summary_cells)

    with __import__("pdfplumber").open(ROOT / made["pdf"]) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert "核查总评" in text and "研报观点与财报数字差异点评" in text
    assert "1741.4" in text


def test_csi_caveat_flags_provisional_and_all_na_dimensions():
    """CSI 口径提示：口径未裁定或存在全「不适用」维度时，必须给出临时/不完整警示。"""
    from src.report import _csi_caveat

    assert _csi_caveat({"na_dimensions": [], "csi": 100.0}) == "", \
        "无全 NA 维度且口径已裁定，不应输出警示"
    c = _csi_caveat({"na_dimensions": ["C间", "C附"], "provisional": True,
                     "profile": "default-provisional"})
    assert "临时" in c and "不完整" in c
    assert "C间" in c and "C附" in c and "2 个维度" in c
    assert "provisional" in c


def test_report_marks_csi_caveat_across_formats():
    """存在全「不适用」维度时，PDF/Excel/网页三格式都必须标注 CSI 为临时/不完整结果。"""
    from openpyxl import load_workbook
    from src.pipeline import ROOT
    from src.report import build_all

    _make_run_fixture("pytest_report_caveat")
    csi_path = ROOT / "runs" / "pytest_report_caveat" / "rules" / "csi.json"
    csi = json.loads(csi_path.read_text(encoding="utf-8"))
    csi.update({"provisional": True, "profile": "default-provisional",
                "na_dimensions": ["C间", "C附"]})
    csi_path.write_text(json.dumps(csi, ensure_ascii=False), encoding="utf-8")
    made = build_all("pytest_report_caveat", ("html", "xlsx", "pdf"))

    html = (ROOT / made["html"]).read_text(encoding="utf-8")
    assert "口径提示" in html and "不完整" in html

    wb = load_workbook(ROOT / made["xlsx"])
    cells = [str(c.value) for row in wb["摘要"].iter_rows() for c in row if c.value]
    assert any(v == "CSI 口径提示" for v in cells)
    assert any("不完整" in v for v in cells)

    with __import__("pdfplumber").open(ROOT / made["pdf"]) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert "口径提示" in text and "不完整" in text


def test_report_no_csi_caveat_when_all_dimensions_applicable():
    """全部维度均可执行且口径已裁定时，报告不得出现「不完整」之类的误导性警示。"""
    from src.pipeline import ROOT
    from src.report import build_all

    _make_run_fixture("pytest_report_no_caveat")
    made = build_all("pytest_report_no_caveat", ("html", "pdf"))

    html = (ROOT / made["html"]).read_text(encoding="utf-8")
    assert "口径提示" not in html and "不完整" not in html
    with __import__("pdfplumber").open(ROOT / made["pdf"]) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert "口径提示" not in text


def test_ratio_records_computed_from_main_statements():
    """研报高频比率（毛利率/净利率/资产负债率）由程序确定性计算，逐期产出作为比对真值。"""
    from src.derived import ratio_records

    recs = ratio_records([
        {"item_code": "REVENUE", "period": "2024", "period_type": "FY", "value": "1000"},
        {"item_code": "OPERATING_COST", "period": "2024", "period_type": "FY", "value": "100"},
        {"item_code": "TOTAL_ASSETS", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "1000"},
        {"item_code": "TOTAL_LIAB", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "400"},
    ])
    by = {(r["item_code"], r["period"]): r for r in recs}
    assert by[("RATIO_GROSS", "2024")]["value"] == "90.0"
    assert by[("RATIO_GROSS", "2024")]["period_type"] == "FY"
    assert by[("RATIO_LEV", "2024-12-31")]["value"] == "40.0"
    assert by[("RATIO_LEV", "2024-12-31")]["period_type"] == "BAL_END"


def test_research_check_compares_percent_ratio_claims():
    """研报引用毛利率这类比率时，按程序计算的真值做确定性比对，而非一律转人工复核。"""
    from src.derived import ratio_records
    from src.research_check import check

    recs = [
        {"item_code": "REVENUE", "period": "2024", "period_type": "FY", "value": "1000"},
        {"item_code": "OPERATING_COST", "period": "2024", "period_type": "FY", "value": "100"},
    ]
    matches = [{"claim_id": "c1", "item_code": "RATIO_GROSS", "period": "2024"},
               {"claim_id": "c2", "item_code": "RATIO_GROSS", "period": "2024"}]
    claims = {"c1": {"sentence": "2024年公司毛利率90.0%，保持稳定"},
              "c2": {"sentence": "2024年公司毛利率95.0%，同比提升"}}
    out = check(matches, claims, recs + ratio_records(recs))
    got = {r["claim_id"]: r for r in out["results"]}
    assert got["c1"]["status"] == "PASS"
    assert got["c2"]["status"] == "FAIL" and got["c2"]["cause"] == "VALUE_ERROR"


def test_new_research_fields_are_extractable_and_in_dictionary():
    """v1.2 新增的研报高频科目必须同时出现在字段字典与主表提取映射里，否则等于没加。"""
    import yaml
    from src.pipeline import ROOT
    from tools.extract_fields import LABEL_ALIASES

    codes = set(yaml.safe_load(
        (ROOT / "rules" / "fields.yaml").read_text(encoding="utf-8"))["fields"])
    extracted = {v[0] for v in LABEL_ALIASES.values()}
    for code in ("CONTRACT_LIAB", "AP", "SHORT_TERM_LOAN", "LONG_TERM_LOAN",
                 "SELLING_EXPENSE", "ADMIN_EXPENSE", "RD_EXPENSE",
                 "FIN_EXPENSE", "TAX_SURCHARGE", "RATIO_GROSS", "RATIO_NET", "RATIO_LEV"):
        assert code in codes, f"{code} 未进字段字典"
    for code in ("CONTRACT_LIAB", "AP", "SHORT_TERM_LOAN", "LONG_TERM_LOAN",
                 "SELLING_EXPENSE", "ADMIN_EXPENSE", "RD_EXPENSE",
                 "FIN_EXPENSE", "TAX_SURCHARGE"):
        assert code in extracted, f"{code} 缺少主表提取映射"


def test_na_dimensions_get_zero_normalized_weight():
    """不适用维度不得显示非零归一化权重——否则报告里会出现误导性的权重数字。"""
    from src.rules_engine import compute_csi
    cfg = {"version": "1.0", "dimensions": {
        "C内": {"weight": 0.25}, "C期": {"weight": 0.15},
        "C间": {"weight": 0.30}, "C附": {"weight": 0.20}, "C研": {"weight": 0.10}}}
    r = compute_csi([{"dimension": "C内", "status": "PASS"},
                     {"dimension": "C期", "status": "PASS"}], cfg)
    for na in ("C间", "C附", "C研"):
        assert r["weights_normalized"][na] == 0.0, na
    assert abs(sum(r["weights_normalized"].values()) - 1.0) < 1e-6


# ---------- H：网页入口 ----------

def test_web_app_renders_without_exception():
    """网页入口必须能渲染且不抛异常（用 Streamlit 官方测试框架真跑一遍）。"""
    from streamlit.testing.v1 import AppTest
    from src.pipeline import ROOT

    _make_run_fixture("pytest_web")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=90).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert [t.value for t in at.title] == ["FinCheck · 财报一致性核查智能体"]

    labels = [m.label for m in at.metric]
    # CSI 指标卡在口径未裁定（provisional）时会带上「（临时）」后缀，故按前缀匹配
    assert any(l.startswith("CSI") for l in labels) and "提取字段" in labels
    assert len(at.tabs) == 5

    # 切到有报告产物的运行，应出现下载按钮
    at.sidebar.selectbox[0].set_value("pytest_web").run()
    assert not at.exception, [str(e.value) for e in at.exception]


def test_web_app_handles_run_without_report():
    """没有报告产物的运行不得报错，而应提示如何去生成。"""
    from streamlit.testing.v1 import AppTest
    from src.pipeline import ROOT

    _make_run_fixture("pytest_web_noreport")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=90).run()
    at.sidebar.selectbox[0].set_value("pytest_web_noreport").run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert any("report" in str(i.value) for i in at.info)


def test_web_upload_orchestrator_persists_and_runs(monkeypatch):
    """网页表单共用的上传编排应抽取、运行离线核查并请求三种报告。"""
    from pathlib import Path
    from src import ingest, pipeline, report
    from src.web import ROOT, run_uploaded_reports

    run_id = "pytest_web_upload_orchestrator"
    run_root = ROOT / "runs" / run_id
    if run_root.exists():
        import shutil
        shutil.rmtree(run_root)

    calls = {}

    class Upload:
        name = "sample_2024.pdf"

        def getvalue(self):
            return b"local-pdf-payload"

    def fake_ingest(folder, out_dir, default_year, report_period=None):
        calls["uploaded"] = (folder / Upload.name).read_bytes()
        calls["year"] = default_year
        calls["period"] = report_period
        return {"structured_files": [out_dir / "sample.json"], "claims_path": None}

    def fake_pipeline(config, **kwargs):
        calls["pipeline"] = (config, kwargs)

    def fake_reports(run, formats):
        calls["reports"] = (run, formats)
        return {f: f"runs/{run}/report.{f}" for f in ("pdf", "xlsx", "html")}

    monkeypatch.setattr(ingest, "process_folder", fake_ingest)
    monkeypatch.setattr(pipeline, "run", fake_pipeline)
    monkeypatch.setattr(report, "build_all", fake_reports)
    progress = []

    try:
        result = run_uploaded_reports(
            [Upload()], run_id=run_id, default_year=2024, offline=True,
            on_progress=progress.append,
        )
        assert calls["uploaded"] == b"local-pdf-payload"
        assert calls["year"] == 2024
        assert calls["period"] == "annual"
        assert calls["pipeline"][1]["offline"] is True
        assert calls["pipeline"][1]["skip_claims"] is True
        assert calls["reports"] == (run_id, ("pdf", "xlsx", "html"))
        assert set(result["reports"]) == {"pdf", "xlsx", "html"}
        assert len(progress) == 3
    finally:
        import shutil
        shutil.rmtree(run_root, ignore_errors=True)


def test_web_upload_rejects_duplicate_names_before_creating_run():
    """无效上传在验证失败时不应留下半成品运行目录。"""
    import shutil
    import pytest
    from src.web import ROOT, run_uploaded_reports

    run_id = "pytest_web_upload_duplicate"
    run_root = ROOT / "runs" / run_id
    shutil.rmtree(run_root, ignore_errors=True)

    class Upload:
        def __init__(self, name):
            self.name = name

        def getvalue(self):
            return b"file"

    with pytest.raises(ValueError, match="文件名重复"):
        run_uploaded_reports(
            [Upload("Report.pdf"), Upload("report.PDF")],
            run_id=run_id, default_year=2024, offline=True,
        )
    assert not run_root.exists()


def test_web_upload_unknown_file_still_builds_report(monkeypatch):
    """上传「任意 PDF/DOCX」而一份年报都没识别出来时不得报错，仍要产出三种报告。

    异常什么结果都不给，用户无从判断；正确行为是照常跑完流程，产出一份
    "未识别到可核查字段"的说明性报告。
    """
    import shutil
    from src import ingest, pipeline, report
    from src.web import ROOT, run_uploaded_reports

    run_id = "pytest_web_upload_no_fields"
    run_root = ROOT / "runs" / run_id
    shutil.rmtree(run_root, ignore_errors=True)
    seen = {}

    class Upload:
        name = "随便一份.pdf"

        def getvalue(self):
            return b"payload"

    monkeypatch.setattr(ingest, "process_folder",
                        lambda folder, out_dir, default_year, report_period=None: {
                            "structured_files": [], "claims_path": None})
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: seen.update(k))
    monkeypatch.setattr(report, "build_all",
                        lambda r, f: {x: f"runs/{r}/report.{x}" for x in f})

    try:
        out = run_uploaded_reports([Upload()], run_id=run_id, default_year=2024, offline=True)
        assert set(out["reports"]) == {"pdf", "xlsx", "html"}
        assert seen, "没识别出年报也必须跑完流程再出报告"
        assert seen["data_dir_override"].replace("\\", "/") == f"runs/{run_id}/ingested"
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_web_upload_threads_report_period(monkeypatch):
    """网页上传必须把「报告期间」透传到解析器，并拒绝非法取值。

    漏传会退回默认 annual：半年报被按年报列头抽取、流量科目标成全年 FY，
    与用户上传的文件对不上——这正是"传进去的报告结论全错"的根因之一。
    """
    import shutil
    import pytest
    from src import ingest, pipeline, report
    from src.web import ROOT, run_uploaded_reports

    run_id = "pytest_web_upload_half_year"
    run_root = ROOT / "runs" / run_id
    shutil.rmtree(run_root, ignore_errors=True)
    seen = {}

    class Upload:
        name = "贵州茅台2026年半年度报告.pdf"

        def getvalue(self):
            return b"payload"

    def fake_ingest(folder, out_dir, default_year, report_period=None):
        seen["period"] = report_period
        return {"structured_files": [], "claims_path": None}

    monkeypatch.setattr(ingest, "process_folder", fake_ingest)
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: None)
    monkeypatch.setattr(report, "build_all", lambda r, f: {})

    try:
        run_uploaded_reports([Upload()], run_id=run_id, default_year=2026,
                             offline=True, report_period="half_year")
        assert seen["period"] == "half_year"
        with pytest.raises(ValueError, match="不支持的报告期间"):
            run_uploaded_reports([Upload()], run_id="pytest_web_upload_bad_period",
                                 default_year=2026, offline=True,
                                 report_period="quarterly")
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_ingest_without_annual_never_falls_back_to_shared_data(monkeypatch):
    """CLI 文件夹入口没识别出年报时，必须仍指向本轮 ingested 目录。

    若回落成 None，pipeline 会去读共享的 data/structured/（旧基线数据），
    产出一份与本次上传文件毫无关系、还看着"很正常"的报告——比空报告更误导人。
    """
    from src import ingest
    import src.pipeline as P

    seen = {}
    monkeypatch.setattr(ingest, "process_folder",
                        lambda folder, out_dir, default_year, report_period: {
                            "files": 1, "log": [], "structured_files": [],
                            "claims_path": None, "claims_count": 0})
    monkeypatch.setattr(P, "run", lambda *a, **k: seen.update(k))

    ingest.ingest("does-not-matter", "pytest_ingest_no_fields", offline=True)
    assert seen["data_dir_override"].replace("\\", "/") == "runs/pytest_ingest_no_fields/ingested"
    assert seen["claims_override"] is None and seen["skip_claims"] is True


def test_csi_includes_research_dimension():
    """C研 结果必须并入 CSI，并按 rules.yaml 的当前维度权重计算。

    此前 C研 的 FAIL 不进 CSI，会出现"查出 2 条研报错误、CSI 却仍是满分"的虚高。
    """
    from src.pipeline import ROOT
    from src.rules_engine import run_rules

    base = ROOT / "runs" / "pytest_csi_research" / "rules"
    base.mkdir(parents=True, exist_ok=True)
    (base / "research_claims.json").write_text(json.dumps({"results": [
        {"dimension": "C研", "status": "PASS"}, {"dimension": "C研", "status": "FAIL"},
    ]}, ensure_ascii=False), encoding="utf-8")

    out = run_rules([
        {"item_code": "TOTAL_ASSETS", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "1000.00", "unit_raw": "元", "raw_text": "资产总计 1000.00"},
        {"item_code": "TOTAL_LIAB", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "400.00", "unit_raw": "元", "raw_text": "负债合计 400.00"},
        {"item_code": "TOTAL_EQUITY", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "600.00", "unit_raw": "元", "raw_text": "所有者权益合计 600.00"},
    ], "pytest_csi_research")

    dims = out["csi"]["dimensions"]
    assert dims["C研"]["rules_applicable"] == 2, "C研 结果未并入 CSI"
    assert dims["C研"]["score"] == 0.5
    assert out["csi"]["csi"] < 100, "研报有 FAIL 时 CSI 不应满分"


def test_csi_grade_boundaries():
    """等级按接口的 90/75/60/40 分边界映射，低于40为E。"""
    from src.rules_engine import compute_csi

    cfg = {"version": "1.0", "dimensions": {"C内": {"weight": 1.0}}}
    for score, expected in ((90.0, "A"), (89.5, "B"), (75.0, "B"),
                            (74.5, "C"), (60.0, "C"), (59.5, "D"),
                            (40.0, "D"), (39.5, "E")):
        passes = int(score)
        reviews = int((score - passes) * 2)
        results = ([{"dimension": "C内", "status": "PASS"}] * passes
                   + [{"dimension": "C内", "status": "REVIEW"}] * reviews
                   + [{"dimension": "C内", "status": "FAIL"}] * (100 - passes - reviews))
        got = compute_csi(results, cfg)
        assert got["csi"] == score, (score, got["csi"])
        assert got["grade"] == expected, (score, got["grade"])


def _provisional_records():
    return [
        {"item_code": "TOTAL_ASSETS", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "1000.00", "unit_raw": "元", "raw_text": "资产总计 1000.00"},
        {"item_code": "TOTAL_LIAB", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "400.00", "unit_raw": "元", "raw_text": "负债合计 400.00"},
        {"item_code": "TOTAL_EQUITY", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "600.00", "unit_raw": "元", "raw_text": "所有者权益合计 600.00"},
    ]


def test_csi_weights_come_from_central_scoring_config():
    """CSI 权重必须来自 rules.yaml 的 scoring 段（集中开关），weights_used 与配置一致。"""
    import yaml
    from src.pipeline import ROOT
    from src.rules_engine import run_rules

    cfg = yaml.safe_load((ROOT / "rules" / "rules.yaml").read_text(encoding="utf-8"))
    expected = {k: v["weight"] for k, v in cfg["scoring"]["dimensions"].items()}

    run_rules(_provisional_records(), "pytest_scoring_cfg")
    csi = json.loads((ROOT / "runs" / "pytest_scoring_cfg" / "rules" / "csi.json")
                     .read_text(encoding="utf-8"))
    assert csi["weights_used"] == expected, \
        "weights_used 未取自集中在 rules.yaml 的 scoring 配置"


def test_csi_json_marks_provisional():
    """口径未获裁定前，csi.json 必须带 provisional 标记与生效口径来源（profile）。"""
    import yaml
    from src.pipeline import ROOT
    from src.rules_engine import run_rules

    cfg = yaml.safe_load((ROOT / "rules" / "rules.yaml").read_text(encoding="utf-8"))
    run_rules(_provisional_records(), "pytest_scoring_cfg")
    csi = json.loads((ROOT / "runs" / "pytest_scoring_cfg" / "rules" / "csi.json")
                     .read_text(encoding="utf-8"))
    assert csi["provisional"] is True, "csi.json 缺少 provisional 标记"
    assert csi["profile"] == cfg["scoring"]["profile"] == "default-provisional"


# ---------- I：异常原因解释 ----------

def _fake_explain(monkeypatch, cause="UNIT_SCALE", text="单位表述不规范", conf=0.9):
    from src import explain
    seen = []

    def fake(task, payload, run_id, **kw):
        seen.append((task, payload))
        return ({"ok": True, "cause": cause, "explanation": text, "confidence": conf,
                 "evidence": {"chunk_id": payload["chunk_id"], "quote": ""}}, "API_OK")

    monkeypatch.setattr(explain, "call_task", fake)
    return explain, seen


def test_explain_only_for_fail_and_review(monkeypatch):
    """只解释 FAIL / REVIEW；PASS 与 NA 不调用模型（省钱且无意义）。"""
    explain, seen = _fake_explain(monkeypatch)
    results = [
        {"rule_id": "A", "status": "PASS", "evidence": {"chunk_id": "t1", "raw_text": "x"}},
        {"rule_id": "B", "status": "NA", "evidence": {"chunk_id": "t2", "raw_text": "x"}},
        {"rule_id": "C", "status": "FAIL", "diff": "2000.00", "tolerance": "100.00",
         "evidence": {"chunk_id": "t3", "raw_text": "资产总计 …"}},
    ]
    explain.explain_anomalies(results, "pytest_explain")
    assert len(seen) == 1 and seen[0][1]["rule_id"] == "C"


def test_explain_rejects_numbers_in_explanation(monkeypatch):
    """模型解释里出现数字即丢弃——prompt 已禁止，程序侧必须兜底。"""
    explain, _ = _fake_explain(monkeypatch, text="差额约为财报值的10倍")
    results = [{"rule_id": "C", "status": "FAIL", "diff": "2000", "tolerance": "100",
                "evidence": {"chunk_id": "t3", "raw_text": "x"}}]
    explain.explain_anomalies(results, "pytest_explain2")
    e = results[0]["explanation"]
    assert e["text"] == "" and "含数字" in e["note"]


def test_explain_flags_disagreement_with_program():
    """模型判断与程序确定性归因不一致时必须标出，不得静默采信。"""
    from src import explain
    import src.explain as E

    class FakeCall:
        @staticmethod
        def call_task(task, payload, run_id, **kw):
            return ({"ok": True, "cause": "VALUE_ERROR", "explanation": "疑为数值引用错误",
                     "confidence": 0.8,
                     "evidence": {"chunk_id": payload["chunk_id"], "quote": ""}}, "API_OK")

    orig = E.call_task
    E.call_task = FakeCall.call_task
    try:
        results = [{"rule_id": "RC01", "status": "FAIL", "cause": "UNIT_SCALE",
                    "diff": "1", "tolerance": "10", "claim_id": "c1",
                    "evidence": {"sentence": "归母净利润 862.3 亿万元"}}]
        explain.explain_anomalies([], "pytest_explain3", research_results=results)
        e = results[0]["explanation"]
        assert e["program_cause"] == "UNIT_SCALE"
        assert e["cause"] == "VALUE_ERROR"
        assert e["agrees_with_program"] is False
    finally:
        E.call_task = orig


def test_diff_desc_is_neutral_for_bad_unit():
    """畸形单位时的差异描述：既不把模型引向"数值差异"，也不直接泄露结论。

    正例："差异不在数值大小上" —— 告诉模型别盯着数字，但不点名是单位问题；
    模型要自己从证据原文（含"亿万元"）推出 UNIT_SCALE。
    """
    from src.explain import _diff_desc
    d = _diff_desc({"reason": "单位写法非法：亿万元", "diff": "1", "tolerance": "10"})
    assert "略超容差" not in d, "不得暗示这是数值大小问题"
    assert "数值" in d, "应明确说明差异不在数值上"
    assert "UNIT_SCALE" not in d and "单位写法非法" not in d, "不得直接把答案写进描述"


# ---------- J：标准答案与准确率 / 可复现性 ----------

def test_accuracy_scope_text_marks_sampling_coverage():
    """准确率文案必须说清：E 是金标准抽样结果，且给出金标准相对字段字典的覆盖率。"""
    from src.report import _accuracy_scope

    none_txt = " ".join(_accuracy_scope(None))
    assert "未计算提取准确率 E" in none_txt

    txt = " ".join(_accuracy_scope({
        "E": 100.0, "correct": 46, "total": 46, "wrong": 0, "missing": 0,
        "gold_file": "贵州茅台_2024_核心字段.json", "gold_entries": 46,
        "dict_size": 86, "dict_coverage": round(46 / 86, 4),
    }))
    assert "抽样准确率" in txt and "不等同于字段字典全量准确率" in txt
    assert "贵州茅台_2024_核心字段.json" in txt
    assert "86 项" in txt and "%" in txt


def test_report_includes_accuracy_section_in_all_formats():
    """提取准确率（E）必须在 PDF / Excel / 网页三种报告中都出现（原来一处都没有）。"""
    from openpyxl import load_workbook
    from src.pipeline import ROOT
    from src.report import build_all

    _make_run_fixture("pytest_report_accuracy")
    made = build_all("pytest_report_accuracy", ("html", "xlsx", "pdf"))

    html = (ROOT / made["html"]).read_text(encoding="utf-8")
    assert "提取准确率（E）" in html

    wb = load_workbook(ROOT / made["xlsx"])
    cells = [str(c.value) for row in wb["摘要"].iter_rows() for c in row if c.value]
    assert any(v == "提取准确率" or "提取准确率" in v for v in cells)

    with __import__("pdfplumber").open(ROOT / made["pdf"]) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert "提取准确率" in text


def test_accuracy_compare_counts_correct_wrong_missing():
    """准确率 = 正确 ÷ 应提取；缺失与错误分别计数，不混为一谈。"""
    from tools.check_accuracy import compare
    gold = {"entries": [
        {"item_code": "A", "item_raw": "甲", "period": "2024", "value": "100.00",
         "basis": "双源一致"},
        {"item_code": "B", "item_raw": "乙", "period": "2024", "value": "200.00",
         "basis": "仅PDF"},
        {"item_code": "C", "item_raw": "丙", "period": "2024", "value": "300.00",
         "basis": "仅PDF"},
    ]}
    records = [
        {"item_code": "A", "period": "2024", "value": "100.00"},   # 正确
        {"item_code": "B", "period": "2024", "value": "999.00"},   # 错误
        # C 缺失
    ]
    r = compare(gold, records)
    assert r["total"] == 3 and r["correct"] == 1
    assert r["wrong"] == 1 and r["missing"] == 1
    assert r["E"] == round(1 / 3 * 100, 2)


def test_accuracy_matches_any_duplicate_value():
    """同一 (科目,期间) 有多条记录时，任一条对上即算正确。"""
    from tools.check_accuracy import compare
    gold = {"entries": [{"item_code": "A", "item_raw": "甲", "period": "2024",
                         "value": "100.00", "basis": "仅PDF"}]}
    records = [{"item_code": "A", "period": "2024", "value": "0"},
               {"item_code": "A", "period": "2024", "value": "100.00"}]
    assert compare(gold, records)["E"] == 100.0


def test_gold_standard_exists_and_is_dual_sourced():
    """标准答案必须存在，且含双来源交叉确认的条目（否则不独立）。"""
    from src.pipeline import ROOT
    p = ROOT / "data" / "gold_standard" / "贵州茅台_2024_核心字段.json"
    if not p.exists():
        return
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["entries"], "标准答案为空"
    assert not d.get("mismatches"), "标准答案两个来源存在冲突，须人工裁定"
    dual = [e for e in d["entries"] if e["basis"].startswith("双源")]
    assert dual, "没有任何双来源交叉确认的条目，独立性问题"
    for e in d["entries"]:
        assert e["evidence"].get("page") is not None, f"{e['item_code']} 缺证据页码"


def test_reproducible_digest_ignores_run_id():
    """可复现性比对必须剔除 run_id——它是运行标识，本就该不同。"""
    import tempfile
    from pathlib import Path as _P
    from tools.check_reproducible import _digest
    d = _P(tempfile.mkdtemp())
    (d / "a.json").write_text('{"run_id":"X","v":[1,2]}', encoding="utf-8")
    (d / "b.json").write_text('{"run_id":"Y","v":[1,2]}', encoding="utf-8")
    (d / "c.json").write_text('{"run_id":"X","v":[1,3]}', encoding="utf-8")
    assert _digest(d / "a.json") == _digest(d / "b.json")
    assert _digest(d / "a.json") != _digest(d / "c.json")


def test_save_manifest_marks_completed(tmp_path, monkeypatch):
    """正常结束落盘后应把 RUNNING 状态收敛为 COMPLETED。"""
    import src.manifest as manifest

    monkeypatch.setattr(manifest, "ROOT", tmp_path)
    monkeypatch.setattr(manifest, "summarize_calls", lambda *_: {
        "api_ok": 0, "cache_hit": 0, "cache_miss": 0, "failed": 0, "total": 0,
    })
    data = {"run_id": "manifest_test", "started_at": "2026-10-12T00:00:00+08:00",
            "status": "RUNNING"}
    path = manifest.save_manifest("manifest_test", data)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["status"] == "COMPLETED"
    assert saved["finished_at"]


def test_error_fixture_meets_interface_contract():
    """植错研报 fixture 有 8–10 条、五类各至少两条，且 CSV 列与契约一致。"""
    import csv
    from tools.build_error_case_fixture import CASES, validate_cases
    from src.pipeline import ROOT

    assert len(CASES) == 10
    assert set(validate_cases(CASES).values()) == {2}
    path = ROOT / "data" / "demo" / "研报错误测试案例_契约核验版.csv"
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == ["claim_id", "错误句", "错误类型", "标准答案", "证据应在页码", "备注"]
    assert len(rows) == 10
    assert {r["错误类型"] for r in rows} == {
        "UNIT_SCALE", "PERIOD_MISMATCH", "SCOPE_MIXED", "VALUE_ERROR", "SIGN_ERROR",
    }
    assert len({r["claim_id"] for r in rows}) == 10


# ---- 异常计数口径一致性：网页指标卡与报告总评必须同源（防"显示 0"复发） ----

def test_merged_anomaly_counts_combines_rules_and_research():
    """「异常 / 待复核」必须合并规则级与研报两个数据源，口径与报告总评一致。

    回归背景：网页指标卡只统计 rules/results.json，而 C研 的研报引用比对结果
    落在 rules/research_claims.json（results + review 两段），于是网页显示 0，
    与报告总评的「不一致 3 条、待复核 41 条」对不上。
    """
    from src.report import merged_anomaly_counts

    rules = [{"status": "PASS"}] * 36 + [{"status": "NA"}] * 8
    research = {"results": [{"status": "PASS"}] * 5 + [{"status": "FAIL"}] * 3,
                "review": [{"status": "REVIEW", "reason": "r"}] * 41}
    assert merged_anomaly_counts(rules, research) == (3, 41)

    # 规则级也有 FAIL/REVIEW 时必须一并计入，不能只算研报那一半
    rules2 = [{"status": "FAIL"}, {"status": "REVIEW"}, {"status": "PASS"}]
    research2 = {"results": [{"status": "FAIL"}], "review": [{"claim_id": "c2"}]}
    assert merged_anomaly_counts(rules2, research2) == (2, 2)

    # review 段不带 status 的历史/测试产物也必须按条数计入（不能因缺字段漏计）
    assert merged_anomaly_counts([], {"review": [{"claim_id": "c"}] * 7}) == (0, 7)

    # 缺产物时不得报错（网页会在没有研报的文件上调用同一函数）
    assert merged_anomaly_counts([], {}) == (0, 0)
    assert merged_anomaly_counts(None, None) == (0, 0)


def test_report_lists_research_review_items_not_only_counts():
    """研报待复核条目必须在报告里逐条列出（原因 + 研报原句），不能只给一个计数。"""
    from openpyxl import load_workbook
    from src.pipeline import ROOT
    from src.report import build_all, research_review_rows

    run_id = "pytest_report_review_items"
    _make_run_fixture(run_id)
    made = build_all(run_id, ("html", "xlsx", "pdf"))

    rows = research_review_rows({"research": json.loads(
        (ROOT / "runs" / run_id / "rules" / "research_claims.json").read_text(encoding="utf-8"))})
    assert len(rows) == 1
    assert rows[0][2] == "模型未返回该条的匹配结果"
    assert "预计2025年收入增长8%" in rows[0][3]

    html = (ROOT / made["html"]).read_text(encoding="utf-8")
    assert "五、研报引用异常与待人工复核" in html
    assert "待人工复核 1 条" in html
    assert "模型未返回该条的匹配结果" in html
    assert "预计2025年收入增长8%" in html

    wb = load_workbook(ROOT / made["xlsx"])
    cells = [str(c.value) for row in wb["待人工复核"].iter_rows() for c in row if c.value]
    assert "模型未返回该条的匹配结果" in cells
    assert any("预计2025年收入增长8%" in v for v in cells), \
        "研报原句须从 evidence.sentence 取，不能因取错字段而恒为空"

    with __import__("pdfplumber").open(ROOT / made["pdf"]) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert "研报引用异常与待人工复核" in text and "待人工复核 1 条" in text
    assert "预计2025年收入增长8%" in text
    # 收尾的「人工复核记录」也须给出合并后的待复核数，不能显示 0
    assert "待人工复核 1 条" in text


def test_reports_show_expected_correct_value_column():
    """四端表格必须给出「应为（正确值）」列：只写哪里错了不够，还要写改成什么才对。"""
    from openpyxl import load_workbook
    from src.pipeline import ROOT
    from src.report import build_all, expected_text, research_review_rows

    run_id = "pytest_report_expected_col"
    _make_run_fixture(run_id)
    made = build_all(run_id, ("html", "xlsx", "pdf"))

    # 取值口径：确定性结论取 expected，模型提示取 expected_value，都没有则「—」
    assert expected_text({"expected": "1721亿元"}) == "1721亿元"
    assert expected_text({"expected_value": "应为 -1.2%"}) == "应为 -1.2%"
    assert expected_text({}) == "—"

    # 待复核行：前四列下标不变，第 5 列为「应为」，位置仍在末位
    rows = research_review_rows({"research": json.loads(
        (ROOT / "runs" / run_id / "rules" / "research_claims.json").read_text(encoding="utf-8"))})
    assert rows[0][3].startswith("预计2025年收入增长8%")
    assert rows[0][4] == "—"                # 该条无终值，不得臆造
    assert rows[0][5]                      # 位置列仍居末位
    assert len(rows[0]) == 6

    html = (ROOT / made["html"]).read_text(encoding="utf-8")
    assert "应为（正确值）" in html and "1741.4亿元" in html

    wb = load_workbook(ROOT / made["xlsx"])
    head = [str(c.value) for c in next(wb["研报引用"].iter_rows())]
    assert "应为（正确值）" in head
    cells = [str(c.value) for row in wb["研报引用"].iter_rows() for c in row if c.value]
    assert "1741.4亿元" in cells
    head_rv = [str(c.value) for c in next(wb["待人工复核"].iter_rows())]
    assert "应为（正确值）" in head_rv

    with __import__("pdfplumber").open(ROOT / made["pdf"]) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert "应为（正确值）" in text and "1741.4亿元" in text


def test_report_flags_unrecognized_files_across_formats():
    """未识别的上传文件必须在三种报告里显式标注，不得静默丢弃。"""
    from openpyxl import load_workbook
    from src.report import ingest_notes

    run_id = "pytest_report_unrecognized"
    _make_run_fixture(run_id)
    log = [{"file": "a_2024.pdf", "kind": "annual", "hits": 3, "action": "抽取 40 条字段"},
           {"file": "审计报告.docx", "kind": "unknown", "hits": 0,
            "action": "跳过：既不像年报也不像研报"},
           # ★ 实测踩过：被识别成年报但一条字段都没抽到，只按 kind 判会静默放行
           {"file": "2025年年度审计报告docx.docx", "kind": "annual", "hits": 3,
            "action": "跳过：未抽到任何核心字段"},
           {"file": "研报.pdf", "kind": "research", "hits": 2, "action": "抽取 0 条 claim"}]
    from src.pipeline import ROOT
    from src.report import build_all, unprocessed_entries
    (ROOT / "runs" / run_id / "ingest_log.json").write_text(
        json.dumps(log, ensure_ascii=False), encoding="utf-8")

    # 未参与核查的判据：未识别 / 处理失败 / 跳过未产出 / 抽取 0 条——四类都算
    assert [e["file"] for e in unprocessed_entries(log)] == [
        "审计报告.docx", "2025年年度审计报告docx.docx", "研报.pdf"]
    assert unprocessed_entries(None) == []

    notes = ingest_notes({"ingest_log": log})
    assert any("审计报告.docx" in n and "未识别" in n for n in notes)
    assert any("未参与字段提取与一致性核查" in n for n in notes)
    # 被识别成年报却没抽到字段的文件，同样要标注"未参与核查"，不得因 kind=annual 静默放行
    assert any("2025年年度审计报告docx.docx" in n
               and "未参与字段提取与一致性核查" in n for n in notes)
    assert not any("a_2024.pdf" in n and "未参与" in n for n in notes)

    made = build_all(run_id, ("html", "xlsx", "pdf"))
    html = (ROOT / made["html"]).read_text(encoding="utf-8")
    assert "材料识别结果" in html and "审计报告.docx" in html

    wb = load_workbook(ROOT / made["xlsx"])
    cells = [str(c.value) for row in wb["摘要"].iter_rows() for c in row if c.value]
    assert any(v == "材料识别" for v in cells)
    assert any("审计报告.docx" in v for v in cells)

    with __import__("pdfplumber").open(ROOT / made["pdf"]) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert "材料识别结果" in text and "审计报告.docx" in text


def test_web_upload_persists_ingest_log(monkeypatch):
    """上传编排必须把分类日志（含"未识别"）落盘到 runs/<run_id>/ingest_log.json。"""
    import shutil
    from src import ingest, pipeline, report
    from src.web import ROOT, run_uploaded_reports

    run_id = "pytest_web_ingest_log"
    run_root = ROOT / "runs" / run_id
    shutil.rmtree(run_root, ignore_errors=True)

    class Upload:
        def __init__(self, name):
            self.name = name

        def getvalue(self):
            return b"payload"

    log = [{"file": "审计报告.docx", "kind": "unknown", "hits": 0,
            "action": "跳过：既不像年报也不像研报"}]
    monkeypatch.setattr(ingest, "process_folder",
                        lambda folder, out_dir, default_year, report_period=None: {
                            "log": log, "structured_files": [], "claims_path": None})
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: None)
    monkeypatch.setattr(report, "build_all", lambda r, f: {})

    try:
        run_uploaded_reports([Upload("审计报告.docx")], run_id=run_id,
                             default_year=2024, offline=True)
        saved = json.loads((run_root / "ingest_log.json").read_text(encoding="utf-8"))
        assert saved == log
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


# ---- 语义任务失败/离线缓存未命中：claim 必须留痕为「待人工复核」 ----

def test_research_check_records_unmatched_claims_as_review():
    """模型未作答的 claim 必须落进「待人工复核」，不能在报告与网页里凭空消失。"""
    import shutil
    from src.pipeline import ROOT
    from src.research_check import run_research_check

    run_id = "pytest_rc_unmatched"
    base = ROOT / "runs" / run_id
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    (base / "claims.json").write_text(json.dumps([
        {"claim_id": "a::1", "sentence": "归母净利润 862.3 亿元",
         "section": "核心观点", "source": "模拟研究报告.docx"},
    ], ensure_ascii=False), encoding="utf-8")
    try:
        out = run_research_check(f"runs/{run_id}/claims.json", [], [], run_id,
                                 unmatched=[{"claim_id": "a::1",
                                             "reason": "模型未返回该条的匹配结果",
                                             "sentence": "归母净利润 862.3 亿元"}])
        assert out["results"] == []
        assert len(out["review"]) == 1
        item = out["review"][0]
        assert item["status"] == "REVIEW" and item["dimension"] == "C研"
        assert item["claim_id"] == "a::1"
        assert "862.3 亿元" in item["evidence"]["sentence"]
        saved = json.loads((base / "rules" / "research_claims.json").read_text(encoding="utf-8"))
        assert len(saved["review"]) == 1 and saved["results"] == []
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_match_claim_returns_unmatched_for_review(monkeypatch):
    """离线缓存未命中时 run_match_claim 必须把未作答的 claim 交回调用方。

    回归背景：unmatched 此前只写进 llm/claim_matches.json，调用方拿不到，
    于是 claim_matches 为空时整段 C研 被跳过（manifest research_check=SKIPPED），
    39 条引用既不进「异常」也不进「待复核」，网页显示 0/0。
    """
    import shutil
    from src import pipeline
    from src.pipeline import ROOT, run_match_claim

    run_id = "pytest_match_claim_unmatched"
    base = ROOT / "runs" / run_id
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    (base / "claims.json").write_text(json.dumps([
        {"claim_id": "a::1", "sentence": "s1"}, {"claim_id": "a::2", "sentence": "s2"},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(pipeline, "call_task",
                        lambda *a, **k: (None, "NO_CACHE_OFFLINE"))
    try:
        matches, status, unmatched = run_match_claim(
            f"runs/{run_id}/claims.json", [], run_id, offline=True)
        assert matches == [] and "NO_CACHE_OFFLINE" in status
        assert [u["claim_id"] for u in unmatched] == ["a::1", "a::2"]
        saved = json.loads(
            (base / "llm" / "claim_matches.json").read_text(encoding="utf-8"))
        assert saved["matches"] == [] and len(saved["review"]) == 2
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_match_claim_dedups_ambiguous_periods_into_review(monkeypatch):
    """同一指标被枚举多个期间、句中又无年份时必须去重并转人工复核。

    回归背景（2025 批）：「资产负债率 | 22.0% | …」被模型展开成 2023/2024/2025
    三个期间，同一数值被拿去比三年的财报，产出 3 条互相矛盾的「异常」。
    """
    import shutil
    from src import pipeline
    from src.pipeline import ROOT, run_match_claim

    run_id = "pytest_match_claim_dedup"
    base = ROOT / "runs" / run_id
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    (base / "claims.json").write_text(json.dumps([
        {"claim_id": "a::1", "sentence": "资产负债率 | 22.0% | 19.5% | 19.3%",
         "source": "模拟研报.docx", "section": "盈利预测"},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        pipeline, "_match_claim_batched",
        lambda *a, **k: ([{"claim_id": "a::1", "item_code": "RATIO_LEV", "period": p}
                          for p in ("2023-12-31", "2024-12-31", "2025-12-31")], ["API_OK"]))
    try:
        matches, _status, unmatched = run_match_claim(
            f"runs/{run_id}/claims.json", [], run_id, offline=True)
        assert matches == [], "同一指标的多个期间应全部转人工复核"
        assert len(unmatched) == 1 and "期间无法对齐" in unmatched[0]["reason"]
        saved = json.loads(
            (base / "llm" / "claim_matches.json").read_text(encoding="utf-8"))
        assert saved["matches"] == [] and len(saved["review"]) == 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_summary_flags_unmatched_research_claims_as_review():
    """有引用却一条都没能自动比对时，总评必须如实标注，不能说"未包含研究报告"。"""
    from src.pipeline import ROOT
    from src.report import build_summary, collect, merged_anomaly_counts

    run_id = "pytest_summary_unmatched_research"
    _make_run_fixture(run_id)
    (ROOT / "runs" / run_id / "rules" / "research_claims.json").write_text(
        json.dumps({"results": [], "review": [
            {"claim_id": f"c{i}", "status": "REVIEW", "reason": "模型未返回该条的匹配结果"}
            for i in range(39)]}, ensure_ascii=False), encoding="utf-8")

    model = collect(run_id)
    text = "\n".join(build_summary(model))
    assert "39 条引用全部转人工复核" in text
    assert "未包含研究报告" not in text
    # 计数口径同步：研报 39 条待复核必须进入网页指标卡的同一个函数
    assert merged_anomaly_counts(model["rule_results"], model["research"])[1] >= 39


def test_summary_total_counts_merge_rule_and_research():
    """核查总评的异常/待复核合计必须合并规则级与研报引用两个来源。

    实测踩过的坑：总评只汇总 rules_per_dim（规则级），研报引用的 5 条不一致被算成 0，
    首页出现"异常 0 条"而正文并列列出 5 条异常——同一份报告自相矛盾。
    """
    from src.pipeline import ROOT
    from src.report import build_summary, collect, merged_anomaly_counts

    run_id = "pytest_summary_merged_counts"
    _make_run_fixture(run_id)          # 规则级 1 FAIL、研报引用 1 FAIL + 1 REVIEW
    try:
        model = collect(run_id)
        text = "\n".join(build_summary(model))
        n_fail, n_review = merged_anomaly_counts(model["rule_results"], model["research"])
        assert (n_fail, n_review) == (2, 1), "夹具口径：规则级 1 FAIL + 研报引用 1 FAIL、1 REVIEW"
        assert "核查总评：异常合计 2 条（规则级 1 条 + 研报引用 1 条）" in text
        assert "待人工复核合计 1 条（规则级 0 条 + 研报引用 1 条）" in text
        # 不得再出现只看规则级的旧口径（规则级异常单独陈述时才有 1 条）
        assert "异常合计 0 条" not in text
        assert "建议优先人工核对上述 2 条异常" in text
    finally:
        import shutil
        shutil.rmtree(ROOT / "runs" / run_id, ignore_errors=True)


def test_report_shows_llm_audit_section():
    """模型语义审计提示必须出现在三种报告里，且**不计入**异常/待复核计数。

    为什么单列：模型结论不可复现，性质是"提示"而非"判定"。混进异常口径会让计数
    随模型波动，评审无法复算；但只写在日志里又等于没写，报告上看不到。
    """
    from openpyxl import load_workbook
    from src.pipeline import ROOT
    from src.report import build_all, collect, merged_anomaly_counts

    run_id = "pytest_report_llm_audit"
    _make_run_fixture(run_id)
    base = ROOT / "runs" / run_id
    (base / "llm").mkdir(parents=True, exist_ok=True)
    (base / "llm" / "audit_findings.json").write_text(json.dumps({
        "run_id": run_id, "audited_claims": 3, "findings": [{
            "rule_id": "RC06@v1.0", "dimension": "C研", "claim_id": "c9",
            "status": "REVIEW", "source": "llm_audit", "cause": "SIGN_ERROR",
            "problem_type": "同比方向写反", "severity": "HIGH",
            "expected_value": "应为 -1.2%", "reason": "模型语义审计提示：同比方向写反",
            "evidence": {"file": "b.docx", "sentence": "营业总收入同比+1.2%",
                         "quote": "同比+1.2%", "page": 4, "para_no": 7,
                         "verified": True}}]}, ensure_ascii=False), encoding="utf-8")

    try:
        model = collect(run_id)
        # ★ 审计条目不得进入异常/待复核口径（口径与夹具原值一致）
        assert merged_anomaly_counts(model["rule_results"], model["research"]) == (2, 1)
        summary = "\n".join(model["summary"])
        assert "语义审计补充" in summary and "1 处" in summary

        made = build_all(run_id, ("html", "xlsx", "pdf"))
        html = (ROOT / made["html"]).read_text(encoding="utf-8")
        assert "模型语义审计提示" in html and "同比方向写反" in html and "同比+1.2%" in html
        wb = load_workbook(ROOT / made["xlsx"])
        assert "语义审计提示" in wb.sheetnames
        with __import__("pdfplumber").open(ROOT / made["pdf"]) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        assert "模型语义审计提示" in text and "同比方向写反" in text
    finally:
        import shutil
        shutil.rmtree(ROOT / "runs" / run_id, ignore_errors=True)


def test_semantic_audit_merges_without_touching_rule_findings():
    """语义审计合并：程序已判过的 (claim_id, cause) 不重复报，其它一律转待复核。

    实测的漏报根因同源：一句话可以同时有单位错与方向错，按 claim 整条排除会把
    第二类错误丢掉。故去重键必须是 (claim_id, cause)，不同原因一律各自保留。
    """
    from src.semantic_audit import _finding_record, _program_keys

    rc = {"results": [{"claim_id": "c1", "cause": "UNIT_SCALE", "status": "FAIL"},
                      {"claim_id": "c2", "cause": "", "status": "FAIL"}],
          "review": [{"claim_id": "c3", "reason": "模型未返回该条的匹配结果"}]}
    keys = _program_keys(rc)
    assert ("c1", "UNIT_SCALE") in keys
    assert ("c2", "UNCERTAIN") in keys      # 无 cause 的按兜底原因计
    assert ("c3", "UNCERTAIN") in keys      # REVIEW 条目同理
    assert ("c1", "SIGN_ERROR") not in keys, "同一 claim 的另一类错误必须仍可被报出"

    rec = _finding_record({"claim_id": "c1", "cause": "SIGN_ERROR",
                           "problem_type": "同比方向写反", "severity": "HIGH",
                           "expected_value": "应为 -1.2%", "reason": "方向相反",
                           "evidence_quote": "同比+1.2%"},
                          {"claim_id": "c1", "sentence": "营业总收入同比+1.2%",
                           "page": 4, "para_no": 2}, "RC06@v1.0")
    assert rec["status"] == "REVIEW" and rec["source"] == "llm_audit"
    assert rec["evidence"]["quote"] == "同比+1.2%" and rec["evidence"]["page"] == 4


def test_semantic_audit_validation_blocks_fabricated_evidence():
    """evidence_quote 不在原句里（编造）时必须被拦下——报告不能出现无法复核的条目。"""
    from src.tasks import ALLOWED_CAUSES, _check_audit_findings

    ctx = {"audit_claims": {"c1": "公司营业总收入同比+1.2%"}, "causes": ALLOWED_CAUSES}
    ok = {"findings": [{"claim_id": "c1", "cause": "SIGN_ERROR",
                        "evidence_quote": "同比+1.2%"}]}
    assert _check_audit_findings(ok, ctx) == []
    bad_quote = {"findings": [{"claim_id": "c1", "cause": "SIGN_ERROR",
                               "evidence_quote": "同比-1.2%"}]}
    assert _check_audit_findings(bad_quote, ctx)
    unknown = {"findings": [{"claim_id": "cX", "cause": "SIGN_ERROR",
                             "evidence_quote": "同比+1.2%"}]}
    assert _check_audit_findings(unknown, ctx)
    bad_cause = {"findings": [{"claim_id": "c1", "cause": "MADE_UP",
                               "evidence_quote": "同比+1.2%"}]}
    assert _check_audit_findings(bad_cause, ctx)


def test_stale_products_are_recomputed_with_current_code():
    """产物由旧版代码产出时，必须用当前代码重算 C研 结论。

    这正是"改了代码但报告里还是没有那个异常"的根因：网页端在同进程内 import
    src.pipeline / src.research_check，模块常驻内存，服务没重启就仍用旧逻辑算。
    指纹不符 → 重算 → 报告里就该出现规则能判出来的那条异常。
    """
    import shutil
    from src.codefingerprint import write_fingerprint
    from src.pipeline import ROOT
    from src.research_check import run_research_check
    from src.refresh import ensure_fresh

    run_id = "pytest_stale_refresh"
    base = ROOT / "runs" / run_id
    (base / "ingested" / "claims").mkdir(parents=True, exist_ok=True)
    (base / "llm").mkdir(parents=True, exist_ok=True)
    # 两年营业总收入：2025 同比真值 = (1012-1000)/1000 ≈ +1.2%
    (base / "ingested" / "fields.json").write_text(json.dumps([
        {"item_code": "TOTAL_REVENUE", "item_raw": "营业总收入", "period": "2024",
         "period_type": "FY", "value": "1000", "page": 9, "raw_text": "营业总收入 1000"},
        {"item_code": "TOTAL_REVENUE", "item_raw": "营业总收入", "period": "2025",
         "period_type": "FY", "value": "1012", "page": 9, "raw_text": "营业总收入 1012"},
    ], ensure_ascii=False), encoding="utf-8")
    (base / "ingested" / "claims" / "report_claims.json").write_text(json.dumps([
        {"claim_id": "c1", "kind": "TEXT", "source": "b.docx", "section": "核心观点",
         "page": 4, "para_no": 2,
         "sentence": "公司2025年营业总收入1012亿元，同比-1.2%"},
    ], ensure_ascii=False), encoding="utf-8")
    (base / "llm" / "claim_matches.json").write_text(json.dumps({
        "matches": [{"claim_id": "c1", "item_code": "TOTAL_REVENUE", "period": "2025",
                     "evidence": {"sentence": "公司2025年营业总收入1012亿元，同比-1.2%",
                                  "page": 4, "para_no": 2}}],
        "review": []}, ensure_ascii=False), encoding="utf-8")
    try:
        # 旧产物：先按"另一份代码"的指纹落盘（这里手动写一个不存在的指纹）
        (base / "rules").mkdir(parents=True, exist_ok=True)
        (base / "rules" / "research_claims.json").write_text(
            json.dumps({"results": [], "review": []}), encoding="utf-8")
        fp = base / "code_fingerprint.json"
        fp.write_text(json.dumps({"fingerprint": "0" * 64, "files": []}), encoding="utf-8")

        res = ensure_fresh(run_id)
        assert res == {"stale": True, "recomputed": True}
        after = json.loads((base / "rules" / "research_claims.json").read_text(encoding="utf-8"))
        fails = [(r.get("rule_id"), r.get("cause"))
                 for r in after["results"] if r.get("status") == "FAIL"]
        assert ("RC02@v1.0", "SIGN_ERROR") in fails, \
            "重算后必须报出「同比方向与财报口径相反」这条异常"

        # 再跑一次：指纹已更新为当前代码 → 不再视为陈旧、不再重算
        write_fingerprint(run_id)
        assert ensure_fresh(run_id) == {"stale": False, "recomputed": False}
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_legacy_products_without_fingerprint_are_recomputed():
    """改动之前跑的旧运行没有指纹文件，也必须自愈。

    使用者打开的多半是"改动前那一轮"的报告，投诉「代码改了这里还是没显示异常」。
    旧产物没有指纹（无法判断版本），但只要它确实做过 C研 比对，就用当前代码重算，
    让报告显示与当前代码一致的结论——这正是修复"比率正负号看不见"的最后一环。
    """
    import shutil
    from src.pipeline import ROOT
    from src.refresh import ensure_fresh

    run_id = "pytest_legacy_refresh"
    base = ROOT / "runs" / run_id
    (base / "ingested" / "claims").mkdir(parents=True, exist_ok=True)
    (base / "llm").mkdir(parents=True, exist_ok=True)
    (base / "rules").mkdir(parents=True, exist_ok=True)
    (base / "ingested" / "fields.json").write_text(json.dumps([
        {"item_code": "TOTAL_REVENUE", "item_raw": "营业总收入", "period": "2024",
         "period_type": "FY", "value": "1000", "page": 9, "raw_text": "营业总收入 1000"},
        {"item_code": "TOTAL_REVENUE", "item_raw": "营业总收入", "period": "2025",
         "period_type": "FY", "value": "1012", "page": 9, "raw_text": "营业总收入 1012"},
    ], ensure_ascii=False), encoding="utf-8")
    (base / "ingested" / "claims" / "report_claims.json").write_text(json.dumps([
        {"claim_id": "c1", "kind": "TEXT", "source": "b.docx", "section": "核心观点",
         "page": 4, "para_no": 2,
         "sentence": "公司2025年营业总收入1012亿元，同比-1.2%"},
    ], ensure_ascii=False), encoding="utf-8")
    (base / "llm" / "claim_matches.json").write_text(json.dumps({
        "matches": [{"claim_id": "c1", "item_code": "TOTAL_REVENUE", "period": "2025",
                     "evidence": {"sentence": "公司2025年营业总收入1012亿元，同比-1.2%",
                                  "page": 4, "para_no": 2}}],
        "review": []}, ensure_ascii=False), encoding="utf-8")
    (base / "rules" / "research_claims.json").write_text(
        json.dumps({"results": [], "review": []}), encoding="utf-8")
    try:
        # 关键：不写 code_fingerprint.json —— 模拟"本次改动之前跑的旧运行"
        assert not (base / "code_fingerprint.json").exists()
        res = ensure_fresh(run_id)
        assert res == {"stale": True, "recomputed": True}
        after = json.loads((base / "rules" / "research_claims.json").read_text(encoding="utf-8"))
        fails = [(r.get("rule_id"), r.get("cause"))
                 for r in after["results"] if r.get("status") == "FAIL"]
        assert ("RC02@v1.0", "SIGN_ERROR") in fails, \
            "旧产物无指纹时也必须自愈，报出「同比方向与财报口径相反」"
        # 自愈后写入指纹 → 下一次读取不再重复重算
        assert ensure_fresh(run_id) == {"stale": False, "recomputed": False}
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ---- MCP 服务：stdio JSON-RPC 往返必须真实可用（评审要求 Tool / MCP 核心模块） ----

def test_mcp_server_stdio_protocol_roundtrip():
    """以子进程启动 `python -m mcp_server`，走真实 stdio JSON-RPC 往返。

    验证六条消息：initialize 协商、通知不响应、tools/list 清单、tools/call
    （确定性工具 calculate_ratios）、resources/list，以及未知方法回 -32601。
    """
    import subprocess
    import sys
    from mcp_server.server import PROTOCOL_VERSION, TOOLS

    root = Path(__file__).resolve().parent
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},   # 通知：不应有响应
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "calculate_ratios", "arguments": {"records": [
             {"item_code": "TOTAL_REVENUE", "period": "2024", "value": "1741.4"},
             {"item_code": "NET_PROFIT", "period": "2024", "value": "862.3"}]}}},
        {"jsonrpc": "2.0", "id": 4, "method": "resources/list"},
        {"jsonrpc": "2.0", "id": 5, "method": "no/such-method"},
    ]
    payload = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages)
    proc = subprocess.run([sys.executable, "-m", "mcp_server"], input=payload,
                          capture_output=True, text=True, cwd=root, timeout=180)
    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(x) for x in proc.stdout.splitlines() if x.strip()]
    assert len(lines) == 5, f"6 条消息里通知不响应，应回 5 条；实际 {len(lines)}"
    by_id = {r["id"]: r for r in lines}

    assert by_id[1]["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert by_id[1]["result"]["serverInfo"]["name"] == "fincheck-mcp"
    assert set(t["name"] for t in by_id[2]["result"]["tools"]) == set(TOOLS)
    assert by_id[3]["result"]["isError"] is False
    assert "metrics" in json.loads(by_id[3]["result"]["content"][0]["text"])
    assert by_id[4]["result"]["resources"]
    assert by_id[5]["error"]["code"] == -32601


# ---------------- 可视化（src/viz.py）：口径一致性 + 渲染 ----------------

def _viz_sample():
    """可视化测试样例：覆盖规则级（C内/C期/C间）与研报级（C研）两个数据源与三种状态。"""
    rules = [
        {"rule_id": "BS01@1.0", "dimension": "C内", "status": "PASS"},
        {"rule_id": "IS01@1.0", "dimension": "C内", "status": "FAIL"},
        {"rule_id": "CF01@1.0", "dimension": "C期", "status": "REVIEW"},
        {"rule_id": "BS02@1.0", "dimension": "C间", "status": "NA"},
    ]
    research = {
        "results": [
            {"claim_id": "c1", "dimension": "C研", "status": "FAIL", "cause": "UNIT_SCALE",
             "item_code": "NET_PROFIT", "period": "2024",
             "left": {"source": "research_report", "raw": "862.3亿万元"},
             "expected": "862.3亿元", "reason": "单位写法有误",
             "evidence": {"file": "b.docx", "page": 1, "para_no": 1, "section": "核心观点",
                          "sentence": "2024 年归母净利润 862.3亿万元"}},
            {"claim_id": "c2", "dimension": "C研", "status": "PASS"},
            {"claim_id": "c3", "dimension": "C研", "status": "REVIEW"},
        ],
        # review 段整段都是「程序无法自动判定」的待复核条目，其中一条不带 dimension
        "review": [{"claim_id": "c4", "dimension": "C研", "reason": "模型未返回匹配结果"},
                   {"claim_id": "c5", "reason": "无对应财报期间"}],
    }
    return rules, research


def _viz_csi():
    """可视化测试样例的 CSI：含 2 个全「不适用」维度，覆盖 NA 的呈现口径。"""
    return {"csi": 80.0, "grade": "B", "grade_label": "基本一致",
            "na_dimensions": ["C间", "C附"], "provisional": True,
            "dimensions": {
                "C内": {"score": 1.0, "rules_applicable": 2, "pass": 1, "fail": 1,
                        "review": 0, "na": 0},
                "C间": {"score": None, "rules_applicable": 0, "pass": 0, "fail": 0,
                        "review": 0, "na": 1},
                "C期": {"score": 0.5, "rules_applicable": 1, "pass": 0, "fail": 0,
                        "review": 1, "na": 0},
                "C附": {"score": None, "rules_applicable": 0, "pass": 0, "fail": 0,
                        "review": 0, "na": 1},
                "C研": {"score": 0.0, "rules_applicable": 1, "pass": 0, "fail": 1,
                        "review": 0, "na": 0}}}


def test_viz_counts_stay_on_single_source():
    """图形口径不得分裂：按维度拆分后的合计必须等于 merged_anomaly_counts。"""
    from src.report import merged_anomaly_counts
    from src.viz import (anomaly_by_dimension, dimension_rows, research_error_rows)

    rules, research = _viz_sample()
    n_fail, n_review = merged_anomaly_counts(rules, research)
    assert (n_fail, n_review) == (2, 4)

    by = anomaly_by_dimension(rules, research)
    assert sum(v["fail"] for v in by.values()) == n_fail
    assert sum(v["review"] for v in by.values()) == n_review
    # 维度顺序固定；C内=1异常、C期=1待复核、C研=1异常+3待复核（含无 dimension 的一条）
    assert [(r["dimension"], r["fail"], r["review"]) for r in dimension_rows(rules, research)] \
        == [("C内", 1, 0), ("C期", 0, 1), ("C研", 1, 3)]
    assert by.get("C附") is None, "NA/PASS 不计入异常分布，不得凭空补零条维度"

    rows = research_error_rows(research)
    assert len(rows) == 1
    assert rows[0]["written"] == "862.3亿万元" and rows[0]["should"] == "862.3亿元"
    assert rows[0]["cause_zh"] == "单位量级错误" and "p1" in rows[0]["location"]


def test_viz_figures_render_and_carry_same_numbers():
    """三张图能渲染，且图上数字与单一口径一致（不适用维度按口径单独标注）。"""
    import pytest
    pytest.importorskip("plotly")
    from src.report import merged_anomaly_counts
    from src.viz import (anomaly_distribution, csi_radar, figures_html,
                         research_error_compare)

    rules, research = _viz_sample()
    csi = _viz_csi()

    radar = csi_radar(csi)
    assert radar is not None
    r = list(radar.data[0].r)
    assert len(r) == 6 and r[0] == r[5] == 100.0          # 首尾闭合
    assert radar.data[1].name.startswith("不适用") and list(radar.data[1].r) == [0.0, 0.0]

    dist = anomaly_distribution(rules, research, csi)
    n_fail, n_review = merged_anomaly_counts(rules, research)
    assert sum(dist.data[0].y) == n_fail and sum(dist.data[1].y) == n_review
    # 传 csi 时横轴补齐 5 个维度，数值仍全部来自单一口径（0 条维度画 0）
    assert len(dist.data[0].x) == 5 and dist.data[0].x[0].startswith("C内")

    err = research_error_compare(research)
    cells = list(err.data[0].cells.values)
    assert cells[0] == ["单位量级错误"]
    assert cells[1] == ["862.3亿万元"] and cells[2] == ["862.3亿元"]

    # 缺数据时必须返回 None（调用方据此跳过），不得抛异常
    assert csi_radar({}) is None
    assert anomaly_distribution([], {}) is None
    assert research_error_compare({}) is None
    assert figures_html({}, [], {}) == ""


def test_viz_pdf_drawings_render_with_chinese_font(tmp_path):
    """PDF 静态图必须可渲染：中文字体显式指定，绘制到真实 PDF 不抛异常。"""
    from reportlab.graphics import renderPDF
    from reportlab.pdfgen import canvas
    from src.viz import (anomaly_distribution_drawing, csi_radar_drawing,
                         register_pdf_font)

    rules, research = _viz_sample()
    csi = _viz_csi()
    assert register_pdf_font() == "STSong-Light"

    drawings = [csi_radar_drawing(csi), anomaly_distribution_drawing(rules, research, csi=csi)]
    assert all(d is not None for d in drawings)
    for d in drawings:
        c = canvas.Canvas(str(tmp_path / "viz.pdf"))
        renderPDF.draw(d, c, 20, 400)
        c.showPage()
        c.save()
    assert (tmp_path / "viz.pdf").stat().st_size > 0
    # 无数据时同样返回 None，不抛异常
    assert csi_radar_drawing({}) is None
    assert anomaly_distribution_drawing([], {}) is None


def test_report_html_embeds_viz_figures():
    """HTML 报告必须内嵌核查图表（Plotly），且报告正文不因图形缺失而受影响。"""
    from src.pipeline import ROOT
    from src.report import build_all

    _make_run_fixture("pytest_report_viz")
    made = build_all("pytest_report_viz", ("html",))
    html = (ROOT / made["html"]).read_text(encoding="utf-8")
    assert "核查图表" in html
    assert "plotly-graph-div" in html and "plotly.js" in html
    # plotly 内嵌的图形数据里中文按 \uXXXX 转义，两种形态都要认
    title = json.dumps("研报纠错对比")[1:-1]
    assert title in html or "研报纠错对比" in html
    assert "核查总评" in html and "CSI" in html

