"""生成契约内的 10 条人工植错研报测试样例及配套错误清单。

本 fixture 是独立、明确标注的合成测试材料，不覆写收到的模拟研究报告和标准答案清单。
财报真值使用本地已核验的 2024/2025 主表与分部记录；所有金额统一展示为亿元。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "demo"

# 2024/2025 真值来自对应年度已核验抽取记录；Forecast delta 依表中原预测和新预测计算。
CASES = [
    {
        "claim_id": "fixture_2024_unit_np", "type": "UNIT_SCALE",
        "bad": "2024年归母净利润为862.3亿万元，同比增长15.38%。",
        "correct": "2024年归母净利润为862.3亿元，同比增长15.38%。",
        "fact": "NP_PARENT 2024 = 86,228,146,421.62 元；报告数字 862.3 正确，单位错。",
        "source": "2024 年年度报告，合并利润表；金标准 data/gold_standard/贵州茅台_2024_核心字段.json。",
    },
    {
        "claim_id": "fixture_2025_unit_revenue", "type": "UNIT_SCALE",
        "bad": "2025年营业总收入为1721千万元，同比下降1.2%。",
        "correct": "2025年营业总收入为1721亿元，同比下降1.2%。",
        "fact": "TOTAL_REVENUE 2025 = 172,054,171,890.91 元。",
        "source": "2025 年年度报告，合并利润表；结构化字段 data/structured/贵州茅台_2025.json。",
    },
    {
        "claim_id": "fixture_period_2024_value_as_2025", "type": "PERIOD_MISMATCH",
        "bad": "2025年营业总收入为1741.4亿元。",
        "correct": "2024年营业总收入为1741.4亿元。",
        "fact": "174,144,069,958.25 元属于 2024 FY，不是 2025 FY。",
        "source": "2024/2025 年年度报告，合并利润表比较列；2024/2025 结构化字段。",
    },
    {
        "claim_id": "fixture_period_2025_value_as_2024", "type": "PERIOD_MISMATCH",
        "bad": "2024年归母净利润为823.2亿元。",
        "correct": "2025年归母净利润为823.2亿元。",
        "fact": "82,320,067,101.68 元属于 2025 FY；2024 FY 为 86,228,146,421.62 元。",
        "source": "2024/2025 年年度报告，合并利润表；data/structured/贵州茅台_2024.json 与 贵州茅台_2025.json。",
    },
    {
        "claim_id": "fixture_scope_moutai_as_company_2024", "type": "SCOPE_MIXED",
        "bad": "2024年公司营业总收入为1459.3亿元。",
        "correct": "2024年公司营业总收入为1741.4亿元。",
        "fact": "145,928,075,955.31 元是茅台酒分产品收入；公司营业总收入为174,144,069,958.25元。",
        "source": "2024 年年度报告，第三节主营业务分产品情况及合并利润表。",
    },
    {
        "claim_id": "fixture_scope_series_as_company_2025", "type": "SCOPE_MIXED",
        "bad": "2025年公司营业总收入为222.7亿元。",
        "correct": "2025年公司营业总收入为1720.5亿元。",
        "fact": "22,274,678,707.16 元是系列酒分产品收入；公司营业总收入为172,054,171,890.91元。",
        "source": "2025 年年度报告，第三节主营业务分产品情况及合并利润表。",
    },
    {
        "claim_id": "fixture_value_moutai_2024", "type": "VALUE_ERROR",
        "bad": "茅台酒2024年营业收入为1495.3亿元。",
        "correct": "茅台酒2024年营业收入为1459.3亿元。",
        "fact": "REVENUE_MOUTAI 2024 = 145,928,075,955.31 元。",
        "source": "2024 年年度报告，第三节主营业务分产品情况，茅台酒行。",
    },
    {
        "claim_id": "fixture_value_series_2025", "type": "VALUE_ERROR",
        "bad": "系列酒2025年营业收入为232亿元。",
        "correct": "系列酒2025年营业收入为222.7亿元。",
        "fact": "REVENUE_SERIES 2025 = 22,274,678,707.16 元。",
        "source": "2025 年年度报告，第三节主营业务分产品情况，系列酒行。",
    },
    {
        "claim_id": "fixture_sign_revenue_yoy_2025", "type": "SIGN_ERROR",
        "bad": "2025年营业总收入同比增长1.2%。",
        "correct": "2025年营业总收入同比下降1.2%。",
        "fact": "(172,054,171,890.91 / 174,144,069,958.25 - 1) × 100 = -1.20%（四舍五入）。",
        "source": "2025 年年度报告，合并利润表 2025/2024 比较数；data/structured/贵州茅台_2025.json。",
    },
    {
        "claim_id": "fixture_sign_forecast_delta_2026", "type": "SIGN_ERROR",
        "bad": "2026E归母净利润预测较原预测上调5.4%。",
        "correct": "2026E归母净利润预测较原预测下调5.4%。",
        "fact": "(84,658 / 89,512 - 1) × 100 = -5.42%，按一位小数为 -5.4%。",
        "source": "2025 模拟研报表1盈利预测调整表：2026E 原预测89,512、新预测84,658（单位百万元）。",
    },
]


def validate_cases(cases: list[dict]) -> dict[str, int]:
    expected = ["UNIT_SCALE", "PERIOD_MISMATCH", "SCOPE_MIXED", "VALUE_ERROR", "SIGN_ERROR"]
    counts = {kind: sum(c["type"] == kind for c in cases) for kind in expected}
    if len(cases) not in range(8, 11) or any(n < 2 for n in counts.values()):
        raise ValueError(f"测试样例不满足接口条数/类别要求: {len(cases)=}, {counts=}")
    if len({c["claim_id"] for c in cases}) != len(cases):
        raise ValueError("claim_id 必须唯一")
    if any(c["type"] not in expected or c["bad"] == c["correct"] for c in cases):
        raise ValueError("错误类型必须来自冻结枚举，且错误句须与标准答案不同")
    return counts


def main() -> None:
    counts = validate_cases(CASES)

    OUT.mkdir(parents=True, exist_ok=True)
    claims = {
        "fixture_id": "fincheck_error_cases_v1",
        "notice": "本材料为竞赛模拟样例，非任何机构发布的研究报告。每条均为人工植入错误，仅用于回归测试。",
        "claims": [{"claim_id": c["claim_id"], "sentence": c["bad"],
                    "source": "模拟研报_植错测试样例.md", "page": 1,
                    "planted_error": c["type"]} for c in CASES],
    }
    (OUT / "研报错误测试案例claims.json").write_text(
        json.dumps(claims, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUT / "研报错误测试案例_真值.json").write_text(
        json.dumps({"fixture_id": claims["fixture_id"], "counts_by_type": counts,
                    "items": CASES}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = [
        "# 贵州茅台财务数据模拟研究报告（错误注入测试样例）",
        "",
        "> 本材料为竞赛模拟样例，非任何机构发布的研究报告。",
        "> 本文件的错误由程序测试样例人工植入，不构成真实研究观点。",
        "",
        "## 模拟报告摘录",
        "",
    ]
    for i, c in enumerate(CASES, 1):
        report.extend([f"{i}. **[{c['claim_id']}]** {c['bad']}", ""])
    report.extend(["## 回归测试说明", "",
                   "配套错误清单位于 `研报错误测试案例_契约核验版.csv`。正确值及财报依据见 `研报错误测试案例_真值.json`。",
                   "每条错误句位于本文件第1页的模拟报告摘录；此页码仅指本测试样例，不映射到原年报页码。", ""])
    (OUT / "模拟研报_植错测试样例.md").write_text("\n".join(report), encoding="utf-8")

    csv_path = OUT / "研报错误测试案例_契约核验版.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["claim_id", "错误句", "错误类型", "标准答案", "证据应在页码", "备注"])
        writer.writeheader()
        for c in CASES:
            writer.writerow({"claim_id": c["claim_id"], "错误句": c["bad"],
                             "错误类型": c["type"], "标准答案": c["correct"],
                             "证据应在页码": 1,
                             "备注": f"人工植入；{c['fact']} 来源：{c['source']}"})
    print(f"生成 {len(CASES)} 条测试样例；类别计数={counts}")
    print(f"CSV: {csv_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
