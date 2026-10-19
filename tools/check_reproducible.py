# tools/check_reproducible.py
"""结果可复现性核验：比对两次运行的产物，给出逐项通过/不通过。

比赛要求"运行结果可复现"。本项目把产物分成两类，核验方式不同：

  确定性产物（纯程序计算）——**必须逐字节一致**
    ingested/*.json          字段提取
    rules/results.json       规则核查明细
    rules/csi.json           分项指标与 CSI

  模型相关产物（含模型语义判断）——真实调用间允许浮动，缓存重放则须一致
    rules/research_claims.json   研报引用匹配与比对
    rules/explanations.json      异常原因解释

比对时不看 run_id：它是运行标识，本就应当不同。

用法：
    python tools/check_reproducible.py --a FINAL --b FINAL2
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

# Windows 控制台常见默认编码为 GBK，无法打印勾/叉等符号；
# 采用 UTF-8 可避免完成比较后在总结行崩溃。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]

DETERMINISTIC = ["rules/results.json", "rules/csi.json"]
MODEL_DEPENDENT = ["rules/research_claims.json", "rules/explanations.json"]


def _digest(path: Path) -> str | None:
    """规范化摘要：剔除 run_id，其余按键排序后哈希。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        data.pop("run_id", None)
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]


def _files(run_id: str) -> list[str]:
    """产物清单 = 固定几项 + 该次运行实际产出的提取文件。"""
    rels = list(DETERMINISTIC) + list(MODEL_DEPENDENT)
    d = ROOT / "runs" / run_id / "ingested"
    if d.exists():
        rels += [f"ingested/{p.name}" for p in sorted(d.glob("*.json"))]
    return rels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="第一次运行的 run_id")
    ap.add_argument("--b", required=True, help="第二次运行的 run_id")
    ap.add_argument("--strict-model", action="store_true",
                    help="要求模型相关产物也一致（用于核验缓存重放）")
    args = ap.parse_args()

    rels = sorted(set(_files(args.a)) | set(_files(args.b)))
    rows, deterministic_failed, model_failed = [], [], []
    for rel in rels:
        da = _digest(ROOT / "runs" / args.a / rel)
        db = _digest(ROOT / "runs" / args.b / rel)
        if da is None and db is None:
            continue
        model_dep = rel in MODEL_DEPENDENT
        ok = da == db
        # 模型相关产物：非严格模式下不一致只提示、不算失败
        verdict = ("一致" if ok else
                   ("浮动（模型相关）" if model_dep and not args.strict_model else "不一致"))
        if not ok:
            (model_failed if model_dep else deterministic_failed).append(rel)
        rows.append((rel, da or "-", db or "-", verdict))

    print(f"比对 runs/{args.a} 与 runs/{args.b}（已剔除 run_id）\n")
    print(f"{'产物':<46}{'A':<26}{'B':<26}判定")
    for rel, da, db, verdict in rows:
        print(f"{rel:<46}{da:<26}{db:<26}{verdict}")

    print()
    failed = deterministic_failed + (model_failed if args.strict_model else [])
    if failed:
        if deterministic_failed:
            print(f"✗ 有 {len(deterministic_failed)} 项确定性产物不一致："
                  f"{', '.join(deterministic_failed)}")
        if args.strict_model and model_failed:
            print(f"✗ 严格模式下有 {len(model_failed)} 项模型相关产物不一致："
                  f"{', '.join(model_failed)}")
        raise SystemExit(1)
    print("✓ 确定性产物全部逐字节一致（字段提取 / 规则核查 / CSI）")
    if args.strict_model:
        print("✓ 严格模式下模型相关产物也一致")
    else:
        print("  （模型相关产物的浮动属预期；用 --strict-model 核验缓存重放）")


if __name__ == "__main__":
    main()
