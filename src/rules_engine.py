# src/rules_engine.py
"""规则核查引擎：只凭一份财报就能产出一致性核查结果。

覆盖四个维度（不需要研报）：
  C内 表内勾稽   —— 同一张表内部的加总/平衡关系
  C间 表间一致   —— 不同报表之间的对应关系
  C期 跨期衔接   —— 本期期初 vs 上期期末
  C附 主表附注   —— 主表数 vs 附注披露数

第五个维度 C研（研报引用）由 src/research_check.py 负责，需要额外提供研报。

金额一律用 Decimal 精确计算，不使用浮点（interfaces.md §1.3）。
规则与容差全部来自 rules/*.yaml，本模块不含任何硬编码的财务判断。
"""
from __future__ import annotations

import ast
import json
import operator
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

_BIN_OPS = {ast.Add: operator.add, ast.Sub: operator.sub,
            ast.Mult: operator.mul, ast.Div: operator.truediv}


class ExpressionError(ValueError):
    """表达式中引用了取不到的字段。"""


def safe_eval(expr: str, env: dict[str, Decimal]) -> Decimal:
    """只允许 Decimal 四则运算的表达式求值。

    规则表达式来自配置文件，不能直接 eval——这里用 AST 白名单，
    除四则运算与已提供的字段名外一律拒绝。
    """
    def ev(node):
        if isinstance(node, ast.BinOp):
            fn = _BIN_OPS.get(type(node.op))
            if fn is None:
                raise ExpressionError(f"不支持的运算符：{type(node.op).__name__}")
            return fn(ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -ev(node.operand)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return Decimal(str(node.value))
        if isinstance(node, ast.Name):
            if node.id not in env:
                raise ExpressionError(f"取不到字段：{node.id}")
            return env[node.id]
        raise ExpressionError(f"表达式含不允许的语法：{ast.dump(node)[:60]}")

    return ev(ast.parse(expr, mode="eval").body)


def _tolerance(profile: dict, unit_floor: dict, left: Decimal, right: Decimal,
               unit_raw: str) -> Decimal:
    """有效容差 = max(绝对下限, 相对比例 × max(|左|,|右|))，必要时再取披露单位下限。"""
    abs_floor = Decimal(str(profile.get("abs", "0")))
    rel = Decimal(str(profile.get("rel", "0")))
    tol = max(abs_floor, rel * max(abs(left), abs(right)))
    if profile.get("floor_by_unit"):
        tol = max(tol, Decimal(str(unit_floor.get(unit_raw, "0"))))
    return tol


def _index(records: list[dict]) -> dict[tuple[str, str, str], dict]:
    """(item_code, period, period_type) -> 记录。

    period_type 必须进键：同一时点 2024-12-31 在两份年报里分别以
    「上期期初」和「本期期末」出现，混在一起就没法做跨期衔接。
    """
    idx = {}
    for r in records:
        idx.setdefault((r["item_code"], r["period"], r.get("period_type", "")), r)
    return idx


def _cross_period_periods(records: list[dict], end_code: str, beg_code: str) -> list[str]:
    """找出可以做跨期衔接的时点：该 period 上既有期末记录、又有期初记录。

    即"某年期末数"在下一年报里以"期初数"再次出现——两者必须相等。
    期末与期初可以用**不同的 item_code**（权益变动表把期末/期初拆成两个编号，
    因为期初数还要参与"报告年度对齐"的勾稽，语义上不是同一个字段）。
    """
    ends = {r["period"] for r in records
            if r.get("period_type") == "BAL_END" and r["item_code"] == end_code}
    begs = {r["period"] for r in records
            if r.get("period_type") == "BAL_BEG" and r["item_code"] == beg_code}
    return sorted(ends & begs)


def _read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _field_periods() -> dict[str, str]:
    """item_code -> 期间性质（FY / BAL_END / BAL_BEG），取自 fields.yaml（唯一真源）。

    报告年度对齐要用它来认"这个字段该取哪一类记录"：
    未分配利润期末是 BAL_END、期初是 BAL_BEG，两个都叫"未分配利润"，
    不看字典就分不出哪个是哪个。
    """
    cfg = yaml.safe_load((ROOT / "rules" / "fields.yaml").read_text(encoding="utf-8"))
    return {c: m.get("period", "FY") for c, m in cfg.get("fields", {}).items()}


def _report_year(period: str, ptype: str) -> int | None:
    """记录归属的**报告年度**：期初余额属于下一年度的报告期。

    2024 年报里"期初未分配利润 172,983,178,300.09（2023-12-31 时点）"，
    报告年度是 2024——它和 2024 年的净利润、利润分配同属一期。
    """
    m = re.match(r"(\d{4})", str(period))
    if not m:
        return None
    y = int(m.group(1))
    return y + 1 if ptype == "BAL_BEG" else y


def _by_report_year(idx: dict, code: str, want: str) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for (c, period, ptype), rec in idx.items():
        if c != code or ptype != want:
            continue
        y = _report_year(period, ptype)
        if y is not None:
            out.setdefault(y, rec)
    return out


def _evidence(rec: dict) -> dict:
    loc = {"file": None, "raw_text": rec.get("raw_text", "")}
    if rec.get("page") is not None:
        loc["page"] = rec["page"]
    if rec.get("locator"):
        loc["locator"] = rec["locator"]
    loc["chunk_id"] = rec.get("chunk_id")
    return loc


def _skipped(base: dict, status: str, reason: str) -> dict:
    """缺字段 / 求值失败的统一形态：左值右值全空，绝不猜数。"""
    return {**base, "status": status, "reason": reason, "left": None,
            "right": None, "diff": None, "tolerance": None, "evidence": None}


def _expression_result(base: dict, rule: dict, env: dict, used: dict,
                       profile: dict, unit_floor: dict) -> dict:
    """表达式规则的求值与判级（两种取值方式共用，避免两处各写一遍而渐渐分叉）。"""
    try:
        diff = safe_eval(rule["expr"], env)
    except (ExpressionError, InvalidOperation, ZeroDivisionError) as e:
        return _skipped(base, "REVIEW", f"表达式无法求值：{e}")
    # 证据取金额最大的那条记录：它最可能是"这笔账的关键凭证"
    ref = max(used.values(), key=lambda r: abs(Decimal(r["value"])))
    peak = max((abs(v) for v in env.values()), default=Decimal("0"))
    tol = _tolerance(profile, unit_floor, peak, peak, ref.get("unit_raw", "元"))
    return {
        **base,
        "status": "PASS" if abs(diff) <= tol else "FAIL",
        "left": {"expr": rule["expr"], "value": str(diff)},
        "right": {"expected": "0", "value": "0"},
        "diff": str(diff), "tolerance": str(tol),
        "fields": {c: str(v) for c, v in env.items()},
        "scope": rule.get("scope", "CONSOLIDATED"),
        "evidence": _evidence(ref),
    }


def _report_year_results(rule: dict, idx: dict, ptypes: dict, rules_cfg: dict,
                         profile: dict, unit_floor: dict) -> list[dict]:
    """按报告年度逐期执行表达式规则，返回 interfaces.md §4.1 形态的结果。

    每个报告年度一条结果：同一期内的期末/期初/时期数取齐后一起代入表达式，
    缺字段的年判 NA（不计入分母），不跨期拿数凑。
    """
    per_code = {c: _by_report_year(idx, c, ptypes.get(c, "FY"))
                for c in rule.get("requires", []) + rule.get("zero_if_absent", [])}
    years = sorted({y for m in per_code.values() for y in m}, reverse=True)
    out = []
    for y in years:
        env: dict[str, Decimal] = {}
        used: dict[str, dict] = {}
        missing = []
        for code in rule.get("requires", []):
            rec = per_code[code].get(y)
            if rec is None:
                missing.append(code)
            else:
                env[code] = Decimal(rec["value"])
                used[code] = rec
        for code in rule.get("zero_if_absent", []):
            rec = per_code[code].get(y)
            if rec is None:
                env[code] = Decimal("0")
            else:
                env[code] = Decimal(rec["value"])
                used[code] = rec
        base = {
            "rule_id": f"{rule['rule_id']}@{rules_cfg['version']}",
            "dimension": rule.get("dimension", ""),
            "period": str(y), "period_type": "REPORT_YEAR",
            "expr": rule.get("expr", ""),
            "severity": rule.get("severity", "MED"),
            "applicable_if": rule.get("applicable_if", "always"),
            "human_review": None,
        }
        if missing:
            out.append(_skipped(base, "NA", f"缺字段：{','.join(missing)}"))
            continue
        out.append(_expression_result(base, rule, env, used, profile, unit_floor))
    return out


def run_rules(records: list[dict], run_id: str, offline: bool = False) -> dict:
    """执行全部规则，返回 {"results": [...], "csi": {...}} 并落盘。

    与 pipeline 的约定：本函数只依赖 records，不调用模型、不联网。
    """
    rules_cfg = yaml.safe_load((ROOT / "rules" / "rules.yaml").read_text(encoding="utf-8"))
    tol_cfg = yaml.safe_load((ROOT / "rules" / "tolerance.yaml").read_text(encoding="utf-8"))
    profiles, unit_floor = tol_cfg["profiles"], tol_cfg.get("unit_floor", {})
    ptypes = _field_periods()
    idx = _index(records)
    results: list[dict] = []

    # 有效规则集开关：集中在 rules.yaml 的 scoring.active_rule_ids。
    # null（缺省）= 执行全部 rules；给出 rule_id 列表则只执行列出的规则。
    active_ids = (rules_cfg.get("scoring") or {}).get("active_rule_ids")
    active_rules = rules_cfg.get("rules", [])
    if active_ids:
        wanted = set(active_ids)
        active_rules = [r for r in active_rules if r.get("rule_id") in wanted]

    for rule in active_rules:
        dim = rule.get("dimension", "")
        tol_name = rule.get("tolerance", "MAIN_TABLE")

        # ---- 报告年度对齐：同一报告期内的时点数与时期数一起算 ----
        # 权益变动表的勾稽要同时用到"期末余额（2024-12-31）""期初余额（2023-12-31）"
        # 和"本年净利润/利润分配（FY 2024）"三种期间，按 (period, period_type)
        # 分组会把它们拆到三个组里、每组都缺字段而误判 NA。
        if rule.get("kind") == "report_year":
            results.extend(_report_year_results(rule, idx, ptypes, rules_cfg,
                                                profiles[tol_name], unit_floor))
            continue

        # ---- 跨期衔接：同一时点，期末数 vs 下一年报的期初数 ----
        if rule.get("kind") == "cross_period":
            code = rule["item"]
            beg_code = rule.get("item_beg", code)
            for period in _cross_period_periods(records, code, beg_code):
                end = idx.get((code, period, "BAL_END"))
                beg = idx.get((beg_code, period, "BAL_BEG"))
                if not end or not beg:
                    continue
                l, r = Decimal(end["value"]), Decimal(beg["value"])
                diff = l - r
                tol = _tolerance(profiles[tol_name], unit_floor, l, r, end.get("unit_raw", "元"))
                results.append({
                    "rule_id": f"{rule['rule_id']}@{rules_cfg['version']}",
                    "dimension": dim, "status": "PASS" if abs(diff) <= tol else "FAIL",
                    "left": {"item_code": code, "period": period,
                             "period_type": "BAL_END", "value": str(l)},
                    "right": {"item_code": beg_code, "period": period,
                              "period_type": "BAL_BEG", "value": str(r)},
                    "diff": str(diff), "tolerance": str(tol),
                    "severity": rule.get("severity", "MED"),
                    "applicable_if": rule.get("applicable_if", "always"),
                    "evidence": _evidence(end),
                    "human_review": None,
                })
            continue

        # ---- 表达式规则：在同一 (period_type, period) 下取数 ----
        # 用该规则所需字段中"最常见"的期别组合，避免把时点数与时期数混算。
        # zero_if_absent 的字段同样要进 env——它们只是"没取到就按 0 计"，
        # 取到了就必须用真实值，否则会算成"所有调节项都是 0"。
        envs: dict[tuple[str, str], dict[str, Decimal]] = {}
        used: dict[tuple[str, str], dict[str, dict]] = {}
        for code in rule.get("requires", []) + rule.get("zero_if_absent", []):
            for (c, period, ptype), rec in idx.items():
                if c != code:
                    continue
                key = (period, ptype)
                envs.setdefault(key, {})[code] = Decimal(rec["value"])
                used.setdefault(key, {})[code] = rec

        for key, env in sorted(envs.items()):
            period, ptype = key
            missing = [c for c in rule.get("requires", []) if c not in env]
            # zero_if_absent：固定模板表（如现金流量表补充资料）逐行印出全部调节项，
            # 空白行表示"本年无此项金额"（=0），与"未披露"不同，故按 0 计。
            # ★ 仅对本规则显式列出的字段生效；`requires` 里的字段照旧缺一判 NA。
            for c in rule.get("zero_if_absent", []):
                env.setdefault(c, Decimal("0"))
            base = {
                "rule_id": f"{rule['rule_id']}@{rules_cfg['version']}",
                "dimension": dim, "period": period, "period_type": ptype,
                "expr": rule.get("expr", ""),
                "severity": rule.get("severity", "MED"),
                "applicable_if": rule.get("applicable_if", "always"),
                "human_review": None,
            }
            if missing:
                # 字段缺失 → 该条不适用，不计入分母（不猜、不判错）
                results.append(_skipped(base, "NA", f"缺字段：{','.join(missing)}"))
                continue
            results.append(_expression_result(base, rule, env, used[key],
                                              profiles[tol_name], unit_floor))

    # ★ C研 的结果由 src/research_check.py 产出（存在单独的 research_claims.json），
    #   必须并入 CSI 计算——rules.yaml 当前给 C研 配置 0.10 权重（最终权重待项目组确认），
    #   不并进来就会出现"有 2 条 FAIL 却显示 C研 不适用"的矛盾。
    research_results = (_read_json(ROOT / "runs" / run_id / "rules" / "research_claims.json")
                        or {}).get("results", [])
    csi = compute_csi(results + research_results, rules_cfg)
    out_dir = ROOT / "runs" / run_id / "rules"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps({"run_id": run_id, "rules_version": rules_cfg["version"],
                    "results": results}, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8")
    (out_dir / "csi.json").write_text(
        json.dumps(csi, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {"results": results, "csi": csi}


def _dimension_weights(rules_cfg: dict) -> dict:
    """CSI 维度与权重的**唯一来源**：rules.yaml 的 scoring.dimensions（集中开关）。

    兼容旧写法：无 scoring 段时回退到顶层 dimensions（供单测直接构造 cfg）。
    改 rules.yaml 的 scoring.dimensions 后重跑即可切换口径，无需改本模块。
    """
    scoring = rules_cfg.get("scoring") or {}
    dims = scoring.get("dimensions") or rules_cfg.get("dimensions") or {}
    return {k: v.get("weight", 0) for k, v in dims.items()}


def compute_csi(results: list[dict], rules_cfg: dict) -> dict:
    """按 interfaces.md §4.5 计算分项指标与综合一致性指数。

    得分 = (通过×1 + 待复核×0.5) ÷ 可执行规则数；不适用(NA)不计入分母。
    归一化权重 = 原权重 ÷ Σ(可执行规则数>0 的维度权重)；全不可执行则输出 null。
    口径未获业务裁定时烙上 provisional 与 profile 标记（集中在 rules.yaml 的 scoring 段）。
    """
    weights = _dimension_weights(rules_cfg)
    scoring = rules_cfg.get("scoring") or {}
    marker = {"provisional": bool(scoring.get("provisional", False)),
              "profile": scoring.get("profile", "unknown")}
    dims: dict[str, dict] = {k: {"rules_applicable": 0, "pass": 0, "fail": 0,
                                 "review": 0, "na": 0, "score": None} for k in weights}
    for r in results:
        d = dims.get(r.get("dimension"))
        if d is None:
            continue
        st = r.get("status")
        if st == "NA":
            d["na"] += 1
            continue
        d["rules_applicable"] += 1
        d[{"PASS": "pass", "FAIL": "fail", "REVIEW": "review"}.get(st, "review")] += 1

    live = {k: v for k, v in dims.items() if v["rules_applicable"] > 0}
    for k, v in live.items():
        n = v["rules_applicable"]
        v["score"] = round((v["pass"] + 0.5 * v["review"]) / n, 4)

    total_w = sum(weights[k] for k in live)
    # 不适用维度必须归零：它不参与 CSI 求和，若仍显示"原权重÷适用权重和"，
    # 报告里会出现"C间 0.75"这种看着像有效权重的数字，误导审阅者。
    norm = {k: (round(weights[k] / total_w, 4) if (total_w and k in live) else 0.0)
            for k in weights}
    if not live or total_w == 0:
        return {"csi_version": rules_cfg["version"], "weights_used": weights,
                "weights_normalized": norm, "dimensions": dims,
                "csi": None, "grade": "NA", "grade_label": "不适用",
                "na_dimensions": sorted(dims), "note": "所有维度均无可执行规则",
                **marker}

    csi = round(sum(live[k]["score"] * norm[k] for k in live) * 100, 1)
    for lo, grade, label in ((90, "A", "高度一致"), (75, "B", "基本一致"),
                             (60, "C", "部分一致"), (40, "D", "一致性较弱")):
        if csi >= lo:
            break
    else:
        grade, label = "E", "严重不一致"
    return {"csi_version": rules_cfg["version"], "weights_used": weights,
            "weights_normalized": norm, "dimensions": dims,
            "csi": csi, "grade": grade, "grade_label": label,
            "na_dimensions": sorted(set(dims) - set(live)),
            "note": "不适用维度的权重已按比例分摊至其余维度",
            **marker}
