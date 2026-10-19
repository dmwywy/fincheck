# fincheck/__main__.py
"""python -m fincheck 入口：把 src.pipeline.run 暴露为命令行。

注意：pipeline.run 只落盘、不打印（库不该往 stdout 写东西）。
运行摘要由本模块负责输出——否则跑完一片空白，使用者会以为失败了。
"""
import argparse
import json
import shutil
from pathlib import Path

from src.pipeline import ROOT, run


def _read(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def print_summary(run_id: str) -> None:
    base = ROOT / "runs" / run_id
    manifest = _read(base / "manifest.json", {}) or {}
    calls = manifest.get("calls", {})

    print(f"运行完成  run_id = {run_id}")

    if calls:
        print(f"  模型调用     api_ok={calls.get('api_ok', 0)}  "
              f"cache_hit={calls.get('cache_hit', 0)}  "
              f"cache_miss={calls.get('cache_miss', 0)}  "
              f"failed={calls.get('failed', 0)}  "
              f"合计={calls.get('total', 0)}")
        if calls.get("cache_miss") and not calls.get("failed"):
            print("               （cache_miss = 离线且无缓存，属预期，相关条目已转人工复核）")

    mappings = _read(base / "llm" / "mappings.json")
    if mappings is not None:
        print(f"  科目归一化   {mappings.get('status', '?')}  "
              f"{len(mappings.get('mappings', []))} 条"
              f"（待复核 {len(mappings.get('review', []))}）")

    cm = _read(base / "llm" / "claim_matches.json")
    if cm is not None:
        print(f"  研报引用匹配 {cm.get('status', '?')}  "
              f"{len(cm.get('matches', []))} 条"
              f"（覆盖 {len({m.get('claim_id') for m in cm.get('matches', [])})}"
              f"/{cm.get('claims_sent', 0)} 条 claim，待复核 {len(cm.get('review', []))}）")

    rc = _read(base / "rules" / "research_claims.json")
    if rc is not None:
        got = {}
        for r in rc.get("results", []):
            got[r.get("status")] = got.get(r.get("status"), 0) + 1
        detail = "  ".join(f"{k}={v}" for k, v in sorted(got.items())) or "无"
        print(f"  C研 数值比对 {detail}（待人工复核 {len(rc.get('review', []))}）")

    rules = _read(base / "rules" / "results.json")
    if rules is not None:
        got = {}
        for r in rules.get("results", []):
            got[r.get("status")] = got.get(r.get("status"), 0) + 1
        detail = "  ".join(f"{k}={v}" for k, v in sorted(got.items()))
        print(f"  规则核查     {len(rules.get('results', []))} 条  {detail}")

    exp = _read(base / "rules" / "explanations.json")
    if exp is not None:
        disagree = [i for i in exp.get("items", [])
                    if (i.get("explanation") or {}).get("agrees_with_program") is False]
        line = f"  异常解释     {exp.get('explained', 0)} 条  {exp.get('status', '')}"
        if disagree:
            line += f"  ⚠ 其中 {len(disagree)} 条与程序归因不一致"
        print(line)

    csi = _read(base / "rules" / "csi.json")
    if csi is not None and csi.get("csi") is not None:
        print(f"  一致性指数   CSI={csi['csi']}  等级 {csi['grade']}（{csi.get('grade_label', '')}）")
        parts = [f"{k}:{v['score']}" for k, v in sorted(csi.get("dimensions", {}).items())
                 if v.get("score") is not None]
        if parts:
            print(f"    分项       {'  '.join(parts)}")
        na = csi.get("na_dimensions") or []
        if na:
            print(f"    不适用维度 {','.join(na)}（无可执行规则，权重已分摊）")

    if rules is None and csi is None and (base / "rules" / "placeholder.json").exists():
        print("  规则核查     跳过（未识别出年报，或无可执行规则）")

    print(f"  产物目录     runs/{run_id}/")
    if not calls or calls.get("total", 0) == 0:
        print("  提示：本轮没有任何模型调用，请检查配置与输入文件是否就位。")


def print_report(run_id: str, formats: str = "pdf,xlsx,html") -> None:
    """生成三种形式的核查报告并打印路径。生成失败不阻断主流程。"""
    from src.report import build_all
    try:
        made = build_all(run_id, tuple(f.strip() for f in formats.split(",") if f.strip()))
    except Exception as e:                                   # noqa: BLE001
        print(f"  报告生成失败   {type(e).__name__}: {e}")
        return
    print(f"  核查报告     " + "  ".join(f"{k}={v}" for k, v in made.items()))


def main(argv=None):
    p = argparse.ArgumentParser(prog="fincheck",
                                description="FinCheck 财报一致性核查智能体")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="运行一次核查")
    r.add_argument("--config", default="configs/demo.yaml", help="配置文件路径")
    r.add_argument("--run-id", default="demo", help="运行编号（YYYYMMDD_用途）")
    r.add_argument("--offline", action="store_true", help="离线模式，只读缓存")

    g = sub.add_parser("ingest", help="给一个文件夹，自动识别材料并跑完整流程")
    g.add_argument("--from", dest="folder", required=True,
                   help="存放材料的文件夹（可混放年报与研究报告）")
    g.add_argument("--config", default="configs/demo.yaml", help="配置文件路径")
    g.add_argument("--run-id", default="ingest", help="运行编号")
    g.add_argument("--offline", action="store_true", help="离线模式，只读缓存")
    g.add_argument("--year", type=int, default=None,
                   help="文件名里没有年份时用它作默认报告期")
    g.add_argument("--period", choices=("annual", "half_year"), default="annual",
                   help="年报或半年报；半年报流量数据按 YTD 期间处理")
    g.add_argument("--report-folder", default=None,
                   help="将本次 runs/<run-id> 全部产物复制到该目录（例如桌面上的结果文件夹）")

    rp = sub.add_parser("report", help="生成核查报告（PDF / Excel / 网页）")
    rp.add_argument("--run-id", required=True)
    rp.add_argument("--formats", default="pdf,xlsx,html",
                    help="逗号分隔，可选 pdf,xlsx,html")

    for sp in (r, g):
        sp.add_argument("--no-report", action="store_true",
                        help="跑完不自动生成报告")

    args = p.parse_args(argv)
    if args.cmd == "run":
        run(args.config, offline=args.offline, run_id=args.run_id)
        print_summary(args.run_id)
        if not args.no_report:
            print_report(args.run_id)
    elif args.cmd == "ingest":
        from src.ingest import ingest
        ingest(args.folder, args.run_id, offline=args.offline,
               config_path=args.config, default_year=args.year,
               report_period=args.period)
        print_summary(args.run_id)
        if not args.no_report:
            print_report(args.run_id)
        if args.report_folder:
            source = ROOT / "runs" / args.run_id
            target = Path(args.report_folder).expanduser().resolve() / args.run_id
            try:
                target.mkdir(parents=True, exist_ok=True)
                for child in source.iterdir():
                    destination = target / child.name
                    if child.is_dir():
                        shutil.copytree(child, destination, dirs_exist_ok=True,
                                        ignore=shutil.ignore_patterns("*.tmp", "*.lock"))
                    elif child.is_file():
                        shutil.copy2(child, destination)
                print(f"  已复制运行产物到 {target}")
            except OSError as e:
                print(f"  复制到指定目录失败：{e}")
                print(f"  本次产物仍保存在 {source}")
    elif args.cmd == "report":
        print_report(args.run_id, args.formats)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
