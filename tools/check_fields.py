# tools/check_fields.py
"""字段字典一致性自检：确保 rules.yaml 引用的字段都在 fields.yaml 中，且无重复 code。"""
import re
import sys
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ROOT / "rules" / "fields.yaml"
RULES = ROOT / "rules" / "rules.yaml"


def load(p: Path) -> dict:
    if not p.exists():
        print(f"[跳过] 未找到 {p.relative_to(ROOT)}")
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def main() -> int:
    f = load(FIELDS)
    codes = set(f.get("fields", {}).keys())
    print(f"字段字典：{len(codes)} 个字段，版本 {f.get('version')}")

    # [1] 字段自身完整性
    # 期间键名以 interfaces.md §7.1 的契约为准：`period`。
    # （此处曾误写成 `period_type`，把 36 个合法字段全报成"不合法"——
    #   检查器与契约不一致时改检查器，不改真源。）
    errs = []
    for code, spec in f.get("fields", {}).items():
        if spec.get("type") is None:
            errs.append(f"{code}: 缺少 type")
        if spec.get("period") not in {"FY", "YTD", "BAL_END", "BAL_BEG"}:
            errs.append(f"{code}: period 不合法（{spec.get('period')}）")
        if spec.get("source") in {"NOTE", "MDNA"} and not spec.get("note_ref"):
            errs.append(f"{code}: source 为 {spec.get('source')} 但缺少 note_ref")
        if spec.get("type") == "amount" and spec.get("unit_hint") in {"%", "元/股"}:
            errs.append(f"{code}: 金额字段不应使用非金额单位")
    print(f"[1] 字段自身检查：{'通过' if not errs else '发现问题'}")
    for e in errs:
        print("   -", e)

    # [2] 规则引用是否都在字典里：requires / zero_if_absent / expr 里的字段名 / item
    r = load(RULES)
    missing, unknown_ops = [], []
    for rule in r.get("rules", []):
        rid = rule.get("rule_id")
        refs = list(rule.get("requires", [])) + list(rule.get("zero_if_absent", []))
        refs += [c for c in (rule.get("item"), rule.get("item_beg")) if c]
        refs += re.findall(r"[A-Za-z_][A-Za-z0-9_]*", rule.get("expr", ""))
        for name in refs:
            if name not in codes:
                missing.append(f"{rid} 引用了未定义字段 {name}")
        # 同一字段既 required 又 zero_if_absent 是自相矛盾的写法：缺了到底算 NA 还是 0？
        both = set(rule.get("requires", [])) & set(rule.get("zero_if_absent", []))
        if both:
            unknown_ops.append(f"{rid} 的字段同时出现在 requires 与 zero_if_absent：{sorted(both)}")
        # kind 只认三种；拼错会让规则**静默变成默认取值方式**而不报错
        if rule.get("kind") not in (None, "cross_period", "report_year"):
            unknown_ops.append(f"{rid} 的 kind 不认识：{rule.get('kind')}")
    print(f"[2] 规则引用检查：{'通过' if not missing else '发现问题'}")
    for m in missing:
        print("   -", m)
    print(f"[3] 规则写法检查：{'通过' if not unknown_ops else '发现问题'}")
    for m in unknown_ops:
        print("   -", m)
    missing = missing + unknown_ops

    # [4] 规则条数（《项目思路》要求 20–30 条）
    n_rules = len(r.get("rules", []))
    print(f"[4] 规则条数：{n_rules} 条（另有 C研 RC01，见 src/research_check.py）")
    if not 20 <= n_rules <= 30:
        print(f"   - 注意：规则条数 {n_rules} 不在 20–30 区间")

    ok = not errs and not missing
    print("=" * 48)
    print("结论：" + ("字段字典与规则表一致" if ok else "存在不一致，请修正后重跑"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())