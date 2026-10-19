# tools/export_fields.py
"""把 rules/fields.yaml 导出为可交付的字段字典表（mapping v1）。

为什么要有这个工具：字段字典要交给对端做映射，手抄必然出错、且一改就不同步。
本工具从 `rules/fields.yaml`（程序侧唯一真源）直接生成，保证交付件与代码一致。

用法：
    python tools/export_fields.py            # 输出到当前目录 field_mapping_v1.md
    python tools/export_fields.py --out <路径>  # 指定输出文件
    python tools/export_fields.py --print    # 只打印，不写文件
"""
import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STATEMENT_ZH = {"BS": "资产负债表", "IS": "利润表", "CF": "现金流量表",
                "EQ": "所有者权益变动表", "NOTE": "附注", "MDNA": "管理层讨论与分析"}
PERIOD_ZH = {"BAL_END": "期末时点", "BAL_BEG": "期初时点",
             "FY": "全年累计", "YTD": "年初至今累计"}

# 附件 field_mapping_report.md 中标注为"信工规格中缺失"的字段——
# 经逐项核对，这些**全部已在 fields.yaml 中定义**，故单列一栏对比。
DRAFT_CLAIMED_MISSING = {
    "TOTAL_REVENUE", "CASH_EQ_END", "CASH_ON_HAND", "CASH_IN_BANK",
    "CASH_OTHER", "AR_GROSS", "AR_BADDEBT", "REVENUE_MOUTAI",
    "REVENUE_SERIES", "TOTAL_SHARES",
}


def build_rows() -> list[dict]:
    fields = yaml.safe_load((ROOT / "rules" / "fields.yaml").read_text(encoding="utf-8"))
    rows = []
    for code, m in fields["fields"].items():
        rows.append({
            "item_code": code,
            "zh": m.get("zh", ""),
            "statement": m.get("statement", ""),
            "period": m.get("period", ""),
            "source": m.get("source", ""),
            "core": "是" if m.get("core") else "",
            "note_ref": m.get("note_ref", ""),
            "draft": "草稿标缺失" if code in DRAFT_CLAIMED_MISSING else "",
        })
    return rows


def render(rows: list[dict], version: str) -> str:
    out = [
        "# 字段字典（mapping v1，程序侧冻结）",
        "",
        f"> **版本**：{version} ｜ **真源**：`rules/fields.yaml` ｜ **本文件由 "
        f"`tools/export_fields.py` 生成，请勿手工编辑**",
        ">",
        "> 字段名、类型、枚举取值若需变更，须先改 `接口契约（interfaces.md）` 并升版本号（§1.4），"
        "再改 `rules/fields.yaml`，然后重新生成本文件。",
        "",
        f"共 **{len(rows)}** 个字段，其中核心字段（`core: true`）"
        f"**{sum(1 for r in rows if r['core'])}** 个。",
        "",
        "## 1. 字段清单",
        "",
        "| 信工字段编号 | 中文名 | 所属报表 | 期间类型 | 取数来源 | 核心 | 附注定位 | 附件草稿标注 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        out.append("| `{}` | {} | {} | {} | {} | {} | {} | {} |".format(
            r["item_code"], r["zh"],
            STATEMENT_ZH.get(r["statement"], r["statement"]),
            PERIOD_ZH.get(r["period"], r["period"]),
            r["source"], r["core"], r["note_ref"] or "—", r["draft"] or "—"))

    missing = [r for r in rows if r["draft"]]
    out += [
        "",
        "## 2. 与附件草稿的差异核对",
        "",
        f"附件 `field_mapping_report.md` 标为「信工规格中缺失」的字段共 "
        f"**{len(DRAFT_CLAIMED_MISSING)}** 个。经逐项核对，"
        f"**其中 {len(missing)} 个实际都已在本字典中定义**：",
        "",
        "| 字段 | 中文名 | 实际状态 |",
        "|---|---|---|",
    ]
    for r in missing:
        out.append(f"| `{r['item_code']}` | {r['zh']} | **已定义**（{STATEMENT_ZH.get(r['statement'], r['statement'])}） |")

    out += [
        "",
        "> 结论：该列与仓库不一致，属**事实性差异**（非口径分歧）。建议数据侧复核后更新。",
        "> 其中 `TOTAL_REVENUE` 还已有结构化数据：2024 年报 p63 提取成功，",
        "> 值 `174,144,069,958.25`，与规则表验算值一致。",
        "",
        "## 3. 待对端补充",
        "",
        "本表为**程序侧**字段编号（`rules/fields.yaml` 的 `item_code`）。",
        "附件中的「项目字段」列使用 `BS-06`/`IS-01` 一类编号，其字典来源未在附件中给出，",
        "故本表**保留该列空缺**，不自造对应关系。请对端提供该字典后由其映射，",
        "或在确认后写入 `接口契约（interfaces.md）§6` 统一维护。",
        "",
        "## 4. 约束（已写入代码断言）",
        "",
        "1. `type=amount` 的值一律为**十进制字符串**，参与计算前 `Decimal(str(v))`，禁止浮点；",
        "2. `source=NOTE` 或 `MDNA` 的字段，记录必须带 `note_ref` 与 `page`，否则不得进入核查；",
        "3. 「未披露」不写入 JSON，**不用 `null` 或 `0` 表示**；",
        "4. 口径（合并/母公司）与期间（时点/时期）不得混用。",
        "",
    ]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", dest="to_stdout", action="store_true")
    ap.add_argument("--out", default="field_mapping_v1.md")
    args = ap.parse_args()

    docs = yaml.safe_load((ROOT / "rules" / "fields.yaml").read_text(encoding="utf-8"))
    text = render(build_rows(), docs.get("version", "1.0"))
    if args.to_stdout:
        print(text)
    else:
        p = ROOT / args.out
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"已导出 {len(build_rows())} 个字段 -> {args.out}")


if __name__ == "__main__":
    main()
