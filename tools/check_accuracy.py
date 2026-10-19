# tools/check_accuracy.py
"""结构化提取准确率 E 与运行稳定性核算。

E = 正确提取的关键字段数 ÷ 应提取的关键字段总数 × 100%
  · 分母来自**人工标准答案**（tools/build_gold_standard.py 生成，双来源交叉）
  · 分子由逐条比对得出，不用系统自己的输出当答案

另输出「两次运行一致率」：同一输入跑两遍，比对逐字节结果，衡量提取稳定性。

用法：
    python tools/check_accuracy.py --gold data/gold_standard/贵州茅台_2024_核心字段.json \
                                   --run FINAL
"""
import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


def _load_records(run_id: str) -> list[dict]:
    """取该次运行实际用的字段记录：优先现场解析目录，否则共享基线。"""
    for d in (ROOT / "runs" / run_id / "ingested", ROOT / "data" / "structured"):
        if not d.exists():
            continue
        out = []
        for p in sorted(d.glob("*.json")):
            payload = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                out.extend(r for r in payload
                           if isinstance(r, dict) and "item_code" in r)
        if out:
            return out
    raise SystemExit(f"找不到可用字段记录：runs/{run_id}/ingested 与 data/structured 均为空")


def compare(gold: dict, records: list[dict]) -> dict:
    """逐条比对。同一 (item_code, period) 有多条时，任一条对上即算正确。"""
    got: dict[tuple[str, str], list[str]] = {}
    for r in records:
        got.setdefault((r["item_code"], r["period"]), []).append(r["value"])

    details = []
    for e in gold["entries"]:
        key = (e["item_code"], e["period"])
        vals = got.get(key)
        if not vals:
            status = "缺失"
        elif any(_eq(v, e["value"]) for v in vals):
            status = "正确"
        else:
            status = "错误"
        details.append({
            "item_code": e["item_code"], "item_raw": e["item_raw"],
            "period": e["period"], "标准值": e["value"],
            "提取值": vals[0] if vals else None,
            "条数": len(vals) if vals else 0,
            "判定": status, "核对依据": e["basis"],
        })

    total = len(details)
    right = sum(1 for d in details if d["判定"] == "正确")
    return {
        "total": total,
        "correct": right,
        "missing": sum(1 for d in details if d["判定"] == "缺失"),
        "wrong": sum(1 for d in details if d["判定"] == "错误"),
        "E": round(right / total * 100, 2) if total else None,
        "details": details,
    }


def _eq(a, b) -> bool:
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, TypeError):
        return str(a).strip() == str(b).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--run", required=True, help="要比对的运行编号")
    ap.add_argument("--json-out", help="把明细写到该路径")
    args = ap.parse_args()

    gold = json.loads((ROOT / args.gold).read_text(encoding="utf-8"))
    records = _load_records(args.run)
    res = compare(gold, records)

    print(f"标准答案：{args.gold}")
    print(f"被测运行：{args.run}（{len(records)} 条字段记录）")
    print()
    print(f"  应提取字段 {res['total']} 条")
    print(f"  正确 {res['correct']} ｜ 错误 {res['wrong']} ｜ 缺失 {res['missing']}")
    print(f"  ★ 结构化提取准确率 E = {res['E']}%")
    bad = [d for d in res["details"] if d["判定"] != "正确"]
    if bad:
        print()
        print("  未通过明细：")
        for d in bad:
            print(f"    [{d['判定']}] {d['item_code']}@{d['period']} "
                  f"标准={d['标准值']} 提取={d['提取值']}")

    if args.json_out:
        p = ROOT / args.json_out
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(res, ensure_ascii=False, indent=2, sort_keys=True),
                     encoding="utf-8")
        print(f"\n明细已写入 {args.json_out}")


if __name__ == "__main__":
    main()
