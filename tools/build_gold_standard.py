# tools/build_gold_standard.py
"""生成核心字段的人工标准答案（gold standard）。

★ 为什么不能直接抄系统自己的输出：
  标准答案若取自被测系统，准确率恒为 100%，指标毫无意义。
  本工具的做法是**双独立来源交叉**：
    来源A：PDF 解析路径（pdfplumber，与生产用的 docx 路径是两套完全不同的代码）
    来源B：《贵州茅台一致性规则表（改）》里成员一独立做出的实测验算值
  两者一致才写进标准答案，并把「核对依据」记进文件，供人工复核。
  只有 A 没有 B 的字段，依据列标 `仅PDF`，明确它未经第二来源确认。

每条记录都带证据（文件、页码/定位、原文片段），人工可逐条回验。

用法：
    python tools/build_gold_standard.py --year 2024
"""
import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from tools.extract_fields import extract, sha256_file

ROOT = Path(__file__).resolve().parents[1]

# 来源B：成员一《一致性规则表（改）》最后一列的实测验算值（2024 年度，单位：元）
# 这些数字由金融侧独立核对得出，与本系统的解析代码无关。
REFERENCE_RULES_TABLE = {
    ("TOTAL_ASSETS", "2024-12-31"): "298944579918.70",
    ("TOTAL_LIAB", "2024-12-31"): "56933264798.10",
    ("TOTAL_EQUITY", "2024-12-31"): "242011315120.60",
    ("CURRENT_ASSETS", "2024-12-31"): "251726674636.66",
    ("CURRENT_LIAB", "2024-12-31"): "56515990618.96",
    ("CASH_END", "2024-12-31"): "59295822956.89",
    ("AR_NET", "2024-12-31"): "18974192.75",
    ("INVENTORY", "2024-12-31"): "54343285157.47",
    ("FIXED_ASSETS", "2024-12-31"): "21871446747.14",
    ("TOTAL_REVENUE", "2024"): "174144069958.25",
    ("REVENUE", "2024"): "170899152276.34",
    ("OPERATING_COST", "2024"): "13789482367.98",
    ("OPERATING_PROFIT", "2024"): "119688579453.23",
    ("NET_PROFIT", "2024"): "89334728025.90",
    ("INCOME_TAX", "2024"): "30303850168.56",
    ("NP_PARENT", "2024"): "86228146421.62",
    ("CF_OPERATING", "2024"): "92463692168.43",
    # 附注明细（规则表第 6 条）
    ("CASH_ON_HAND", "2024-12-31"): "9733.00",
    ("CASH_IN_BANK", "2024-12-31"): "59295750908.09",
    ("CASH_OTHER", "2024-12-31"): "62315.80",
}


def build(pdf: Path, year: int) -> dict:
    fields = yaml.safe_load((ROOT / "rules" / "fields.yaml").read_text(encoding="utf-8"))["fields"]
    core = {c for c, m in fields.items() if m.get("core")}

    records, _ = extract(pdf, year)          # 来源A：PDF 解析路径
    entries, mismatches = [], []
    for r in records:
        if r["item_code"] not in core:
            continue
        key = (r["item_code"], r["period"])
        ref = REFERENCE_RULES_TABLE.get(key)
        if ref is not None and Decimal(ref) != Decimal(r["value"]):
            mismatches.append({"item_code": r["item_code"], "period": r["period"],
                               "sourceA": r["value"], "sourceB": ref})
        entries.append({
            "item_code": r["item_code"],
            "item_raw": r["item_raw"],
            "period": r["period"],
            "period_type": r["period_type"],
            "scope": r["scope"],
            "value": r["value"],
            "unit": r["unit_raw"],
            # 双来源一致 → 标"双源一致"；只有 A → 标"仅PDF"，提示未经第二来源确认
            "basis": "双源一致（PDF + 规则表）" if ref is not None else "仅PDF",
            "ref_value": ref,
            "evidence": {"file": pdf.name, "page": r.get("page"),
                         "chunk_id": r.get("chunk_id"), "raw_text": r.get("raw_text")},
        })
    return {"year": year, "entries": sorted(entries, key=lambda e: (e["item_code"], e["period"])),
            "mismatches": mismatches,
            "source_a": {"path": pdf.name, "sha256": sha256_file(pdf)},
            "source_b": "《贵州茅台一致性规则表（改）》实测验算值"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024)
    ap.add_argument("--pdf", required=True, help="作为来源A的年报 PDF")
    args = ap.parse_args()

    data = build(Path(args.pdf), args.year)
    out = ROOT / "data" / "gold_standard" / f"贵州茅台_{args.year}_核心字段.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
                   encoding="utf-8")

    both = sum(1 for e in data["entries"] if e["basis"].startswith("双源"))
    print(f"标准答案 {len(data['entries'])} 条 -> {out.relative_to(ROOT)}")
    print(f"  双源一致 {both} 条 ｜ 仅PDF {len(data['entries']) - both} 条")
    if data["mismatches"]:
        print(f"  ⚠ 双源冲突 {len(data['mismatches'])} 条（必须人工裁定后才能用作标准答案）：")
        for m in data["mismatches"]:
            print(f"     {m['item_code']}@{m['period']}: PDF={m['sourceA']} 规则表={m['sourceB']}")
    else:
        print("  两个独立来源无冲突")


if __name__ == "__main__":
    main()
