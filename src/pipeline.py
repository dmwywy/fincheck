# src/pipeline.py
"""流程编排：把「解析数据 → 语义提取 → 规则核查 → 量化评分 → 报告」串成可运行闭环。

对接边界（务必与接口契约 interfaces.md 保持一致）：
  - 数字经济A  产出 data/structured/<公司>_<期间>.json（§2.1，含证据三要素 page/chunk_id/raw_text）
  - 信息工程    本模块调度模型侧任务，产出 runs/<run_id>/llm/*（§3.1 / §3.2）
  - 数字经济B  实现 src.rules_engine（金额计算 + 表内勾稽 + 表间比对 + CSI 评分，§4）

B 交付 rules_engine 前，本模块用 import 守卫：存在则调用，不存在则写占位文件并跳过，
保证模型侧可独立跑通、可离线复现。
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from src.inputs import MAX_ITEMS, build_match_claim, build_match_item
from src.research_check import dedup_claim_matches, run_research_check
from src.tasks import call_task, load_prompt
from src.manifest import new_manifest, save_manifest

ROOT = Path(__file__).resolve().parents[1]


def load_config(path: str) -> dict:
    with (ROOT / path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_structured(data_dir: str) -> list[dict]:
    """加载 A 的解析结果目录下所有 .json，拍平成一个字段记录数组。"""
    d = ROOT / data_dir
    if not d.exists():
        raise FileNotFoundError(
            f"解析数据目录不存在：{d}。请数字经济A 先产出结构化数据"
            f"（格式见接口契约 §2.1，缺 page/chunk_id/raw_text 不得进入核查）。")
    records = []
    for p in sorted(d.glob("*.json")):
        if p.name == "chunk_index.json":
            continue        # 配套的 chunk_id → 页码映射表，不是字段记录（§2.3）
        payload = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            continue        # 非数组一律跳过
        # 只收"字段记录"：必须带 item_code 与 value。
        # 目录里可能同时有研报 claims（也是数组），不按形状过滤会被当成字段记录，
        # 下游 _claim_fields 取 r["item_code"] 时直接 KeyError。
        records.extend(r for r in payload
                       if isinstance(r, dict) and "item_code" in r and "value" in r)
    return records


def _standard_items() -> list[dict]:
    """标准科目清单（code + 中文名），供 match_item 提示词使用。"""
    with (ROOT / "rules" / "fields.yaml").open(encoding="utf-8") as f:
        fields = yaml.safe_load(f)["fields"]
    return [{"code": c, "name": m.get("zh", c)} for c, m in fields.items()]


def _chunk_map(records: list[dict]) -> dict[str, str]:
    """chunk_id -> raw_text，供证据真实性校验使用（模型只能引用见过的 chunk）。"""
    return {r["chunk_id"]: r.get("raw_text", "") for r in records if r.get("chunk_id")}


def _write_json(run_id: str, rel: str, obj) -> Path:
    p = ROOT / "runs" / run_id / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True),
                 encoding="utf-8")
    return p


def _attach_evidence(items: list[dict], records: list[dict]) -> list[dict]:
    """程序回填证据：模型只给语义映射，科目的来源位置由解析记录确定性回填并校验。

    依据 interfaces.md §5.3 禁则3「模型不生成页码」——位置是客观事实，由程序产出；
    模型复述 chunk_id 没有信息增益，反而多一条"抄错就误杀正确映射"的路径。
    每个来源都做包含性校验：科目名必须真实出现在该片段的 raw_text 中。
    """
    by_raw: dict[str, list[dict]] = {}
    for r in records:
        if r.get("item_raw"):
            by_raw.setdefault(r["item_raw"], []).append(r)

    enriched = []
    for m in items:
        raw = m.get("raw_header", "")
        sources = []
        for r in by_raw.get(raw, []):
            chunk = r.get("raw_text", "")
            sources.append({
                "page": r.get("page"),
                "chunk_id": r.get("chunk_id"),
                "raw_text": chunk,
                "scope": r.get("scope"),
                "period": r.get("period"),
                # 证据校验：原文片段必须真实存在，否则不得用于报告
                "verified": bool(raw) and raw in chunk,
            })
        enriched.append({
            **m,
            "evidence": {
                "sources": sources,
                "verified": bool(sources) and all(s["verified"] for s in sources),
            },
        })
    return enriched


def run_match_item(records: list[dict], run_id: str, offline: bool,
                   api_key: str | None = None):
    """科目归一化：把 A 的原始科目名映射到标准科目编号（模型侧第一步，始终可跑）。
    模型返回映射 → 程序回填来源证据 → 落盘 llm/mappings.json。

    ★ 分批：一次最多 MAX_ITEMS 个科目名（inputs.py 的硬上限）。
      规则库扩到 27 条后，一份年报的科目名个数已超过 40 个，
      不分批会直接触发 build_match_item 的断言、整条流程走不完。
    """
    headers = sorted({r["item_raw"] for r in records if r.get("item_raw")})
    if not headers:
        return None, "NO_HEADERS"
    standard = _standard_items()
    merged, statuses = [], []
    for i in range(0, len(headers), MAX_ITEMS):
        out, status = call_task("match_item",
                                build_match_item(headers[i:i + MAX_ITEMS], standard),
                                run_id, offline=offline, api_key=api_key)
        statuses.append(status)
        if out:
            merged.extend(out["items"])
    status = "|".join(dict.fromkeys(statuses))

    mappings = _attach_evidence(merged, records)
    unverified = [m["raw_header"] for m in mappings
                  if m["evidence"]["sources"] and not m["evidence"]["verified"]]
    _write_json(run_id, "llm/mappings.json",
                {"run_id": run_id, "status": status,
                 "headers_sent": len(headers),
                 "mappings": mappings,
                 # 无来源/校验未过 → 不能进报告，只能进人工复核清单
                 "review": [{"item_raw": h, "reason": "无解析记录或原文片段校验未通过"}
                            for h in unverified]})
    return out, status


def _claim_fields(records: list[dict]) -> list[dict]:
    """可得指标清单：**只给科目与期间，不给金额**（interfaces.md §5.1 关键约束）。
    给了金额，模型就可能顺手判对错，越界到规则侧的职责。"""
    seen, out = set(), []
    for r in records:
        key = (r["item_code"], r["period"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"item_code": r["item_code"], "period": r["period"]})
    return sorted(out, key=lambda x: (x["item_code"], x["period"]))


def _attach_claim_evidence(matches: list[dict], claim_by_id: dict) -> list[dict]:
    """程序回填引用位置：研报文件名 + 章节 + **页码/段号/表行号/表标题** + 原句。

    ★ 位置必须落到"页码 + 段落/表行"，只给章节不够：研报一段里常挤着多条引用，
      "核心观点第3段"这种粒度无法让人复核到底指哪一句（比赛要求指明页码与段落）。
     ★ page 是**推定页码**（docx 无渲染页码，按显式分页符/分节符推定，见
      tools/extract_claims.read_blocks），报告里一律标注为"推定"。
     """
    out = []
    for m in matches:
        c = claim_by_id.get(m.get("claim_id"))
        out.append({
            **m,
            "evidence": {
                "file": c["source"] if c else None,
                "section": c["section"] if c else None,
                "sentence": c["sentence"] if c else None,
                "page": c.get("page") if c else None,
                "para_no": c.get("para_no") if c else None,
                "row_no": c.get("row_no") if c else None,
                "caption": c.get("caption") if c else None,
                # 模型返回了不存在的 claim_id → 证据无效，不得进入核查
                "verified": c is not None,
            },
        })
    return out


CLAIM_BATCH = 20     # 起始批大小；失败时二分递减（见 _match_claim_batched）


def _match_claim_batched(claims: list[dict], fields: list[dict],
                         run_id: str, offline: bool,
                         api_key: str | None = None) -> tuple[list[dict], list[str]]:
    """分批调用 match_claim，并对"输出被截断"做自适应二分。

    deepseek-flash 默认开启深度思考，reasoning 与正文共享 max_tokens；若某批的
    推理量把预算烧光，正文会返回空串（OUTPUT_EMPTY，见 src/llm.py 的 THINKING）。
    已在调用层关闭思考，正常批次开销很小；此处保留二分作为兜底——推理量并非完全
    确定，拆半能把"整批丢失"降级为"个别条目进人工复核"。
    """
    out, status = call_task(
        "match_claim",
        build_match_claim([{"claim_id": c["claim_id"], "sentence": c["sentence"]}
                           for c in claims], fields),
        run_id, offline=offline, api_key=api_key)
    # 只有"输出被截断"才值得拆小重试。缓存未命中、凭据错误这类失败，
    # 拆得再小也不会变好——不判断就会在离线模式下裂变成指数级调用。
    if out or len(claims) == 1 or "OUTPUT_EMPTY" not in status:
        return (out["matches"] if out else []), [status]

    mid = len(claims) // 2
    m1, s1 = _match_claim_batched(claims[:mid], fields, run_id, offline, api_key=api_key)
    m2, s2 = _match_claim_batched(claims[mid:], fields, run_id, offline, api_key=api_key)
    return m1 + m2, s1 + s2


def run_match_claim(claims_path: str, records: list[dict], run_id: str, offline: bool,
                    api_key: str | None = None):
    """研报引用匹配：claim 句子 → (item_code, period)。数值比较交给规则侧（C研 维度）。

    返回 (matches, status, unmatched)：unmatched 是模型没作答的 claim（离线缓存未命中、
    输出截断等），必须交回调用方一并记成「待人工复核」，不能只存在 claim_matches.json 里。
    """
    p = ROOT / claims_path
    if not p.exists():
        return None, "NO_CLAIMS", []
    all_claims = json.loads(p.read_text(encoding="utf-8"))
    # ★ 只有"可核查的引用"才送模型：TEXT（正文句）与 TABLE_ROW（带单位的表行）。
    #   TABLE_HEADER（年份列头）/ TABLE_LABEL（行标签+裸数字，单位写在标签里）不含可与
    #   财报对齐的数值，送模型只会平白多出"未匹配"待复核条目；它们专供
    #   src.research_check 的单位/自校验检测器读取。
    #   用"排除法"而非白名单：旧版 claims 文件可能没有 kind 字段，白名单会把它们整批丢掉。
    all_claims = [c for c in all_claims
                  if c.get("kind") not in ("TABLE_HEADER", "TABLE_LABEL")]
    if not all_claims:
        return None, "NO_CLAIMS", []

    fields = _claim_fields(records)
    matches, flat_status = [], []
    for i in range(0, len(all_claims), CLAIM_BATCH):
        m, s = _match_claim_batched(all_claims[i:i + CLAIM_BATCH], fields, run_id, offline,
                                    api_key=api_key)
        matches.extend(m)
        flat_status.extend(s)
    claims = all_claims

    claim_by_id = {c["claim_id"]: c for c in claims}
    enriched = _attach_claim_evidence(matches, claim_by_id)
    # 去多义化：同一指标在同句被枚举多个期间时只留年份对得上的；句中无年份可对应
    # （列头年份在抽取时丢失，典型是预测表行）则整条转人工复核，不硬判对错。
    enriched, ambiguous = dedup_claim_matches(enriched)
    matched_ids = {m["claim_id"] for m in enriched}
    ambiguous_ids = {a["claim_id"] for a in ambiguous}
    # 模型漏答的 claim：不猜测，一律进人工复核
    unmatched = [{"claim_id": c["claim_id"], "reason": "模型未返回该条的匹配结果",
                  "sentence": c["sentence"]}
                 for c in claims
                 if c["claim_id"] not in matched_ids
                 and c["claim_id"] not in ambiguous_ids]
    unmatched.extend(ambiguous)
    _write_json(run_id, "llm/claim_matches.json", {
        "run_id": run_id,
        "status": "|".join(dict.fromkeys(flat_status)),
        "claims_sent": len(claims),
        "fields_offered": len(fields),
        "matches": enriched,
        "review": unmatched,
    })
    return enriched, "|".join(dict.fromkeys(flat_status)), unmatched


def explain_summary(run_id: str) -> str:
    """异常解释的条数摘要。

    ★ 没有 explanations.json 是**正常情况**，不是错误：一轮下来一条 FAIL/REVIEW 都没有时
      `explain_anomalies` 不会落盘（没有异常就没有需要解释的条目，见 src/explain.py）。
      此前这里无条件读文件，导致"数据干净、零异常"的运行在最后一步崩掉——
      越干净的输入越走不通，必须容忍。
    """
    p = ROOT / "runs" / run_id / "rules" / "explanations.json"
    try:
        return f"{json.loads(p.read_text(encoding='utf-8')).get('explained', 0)} 条"
    except (OSError, json.JSONDecodeError):
        return "无异常，无需解释"


def run_rules_engine(records: list[dict], run_id: str, offline: bool):
    """规则侧钩子：数字经济B 交付 src.rules_engine 后在此接入。

    约定函数签名：run_rules(records, run_id, offline) -> dict
      返回 {"results": [...], "csi": {...}}，字段见 interfaces.md §4。
    B 未交付时返回 None 并写占位文件，不阻断模型侧流程。
    """
    try:
        from src.rules_engine import run_rules
    except ImportError:
        _write_json(run_id, "rules/placeholder.json",
                    {"note": "src.rules_engine 未就绪（数字经济B 待交付）；本轮跳过规则核查",
                     "results": [], "csi": None})
        return None
    return run_rules(records, run_id, offline)


def run_commentary(records: list[dict], rule_res: list[dict],
                   research_res: list[dict], csi: dict,
                   run_id: str, offline: bool,
                   api_key: str | None = None) -> str:
    """研报式评价：把程序已算好的经营指标值 + 派生计算值（同比/比率/亿元/测算）+ CSI
    + 异常 + 研报比对喂给模型，让它写结构化分析评价（数字白名单校验）。
    失败/离线不中断，返回状态字符串。"""
    from src.derived import compute_derived
    from src.inputs import build_gen_commentary, collect_allowed_numbers
    names = {c: n for c, n in _standard_items()}
    metrics = [{
        "code": r.get("item_code", ""),
        "name": names.get(r.get("item_code", ""), r.get("item_raw", "") or r.get("item_code", "")),
        "period": r.get("period", ""),
        "value": r.get("value", ""),
    } for r in records if r.get("item_code")]

    anomalies = [r for r in rule_res if r.get("status") in ("FAIL", "REVIEW")]
    research_fail = [r for r in research_res if r.get("status") == "FAIL"]

    # ★ 派生指标由程序确定性计算（同比/比率/亿元换算/情景测算），
    #   随 payload 一起喂给模型，其数值自动进入数字白名单；
    #   模型只负责组织语言，不得自行做任何运算。
    derived = compute_derived(records)
    # 确定性财务分析（盈利能力/费用率/趋势/结构），同样作为上下文喂给模型；
    # 与 derived 同 code 的条目在 build_gen_commentary 内按 code 去重（derived 优先）。
    from src.financial_analysis import analyze as analyze_financials
    analysis = analyze_financials(records)

    payload = build_gen_commentary(metrics, csi or {}, anomalies, research_fail,
                                   (csi or {}).get("grade_label", ""), derived, analysis)
    allowed = collect_allowed_numbers(payload)
    out, status = call_task("gen_commentary", payload, run_id, offline=offline,
                            context={"allowed_numbers": allowed}, api_key=api_key)
    _write_json(run_id, "llm/commentary.json", {
        "run_id": run_id,
        "status": status,
        "derived_count": len(derived),
        "analysis_count": len(analysis.get("metrics") or []),
        "analysis_warnings": analysis.get("warnings") or [],
        "text": (out or {}).get("text", "") if out else "",
    })
    return status


def run(config_path: str, offline: bool = False, run_id: str = "demo",
        claims_override: str | None = None,
        data_dir_override: str | None = None,
        skip_claims: bool = False,
        api_key: str | None = None) -> dict:
    """入口：加载配置 → 解析数据 → 语义提取 → 规则核查（钩子）→ 落盘清单。

    claims_override / data_dir_override：由文件夹入口（src.ingest）传入本次现场
    解析出来的数据位置。必须覆盖默认的共享目录——共享目录是"策展好的基线"，
    而 load_structured 会把目录下所有 json 全读进来，现场材料混进去会串数据。
    """
    cfg = load_config(config_path)
    data_dir = (data_dir_override
                or cfg.get("paths", {}).get("structured", "data/structured"))
    materials = cfg.get("run", {}).get("materials", {})
    claims_path = "" if skip_claims else (claims_override or materials.get("report_claims", ""))
    operator = cfg.get("run", {}).get("user", "team")

    records = load_structured(data_dir)
    # 研报比对另有可用指标：程序确定性计算的比率（毛利率/净利率/资产负债率）。
    # 只喂给"研报匹配 + C研 比对"，不动 match_item 与规则引擎的输入——
    # 比率不是财报原文科目，混进规则引擎会造成无意义的 NA。
    from src.derived import ratio_records
    check_records = records + ratio_records(records)

    # 运行清单：只记元信息，不记密钥、不记财报正文
    _, meta_item = load_prompt("match_item")
    _, meta_claim = load_prompt("match_claim")
    manifest = new_manifest(run_id, operator,
                            model_cfg={"provider": "deepseek",
                                       "model_id": "deepseek-flash"},
                            prompts=[meta_item, meta_claim], input_files=[])

    # 1. 科目归一化（财报原文科目名 → 标准科目编号）
    _, s_item = run_match_item(records, run_id, offline, api_key=api_key)
    # 2. 研报引用匹配（claim 句子 → 科目+期间）：模型只答"在说哪个指标"
    claim_matches, s_claim, claim_unmatched = (
        run_match_claim(claims_path, check_records, run_id, offline, api_key=api_key)
        if claims_path else (None, "NOT_CONFIGURED", []))
    # 2b. C研 维度数值比对（确定性计算，模型不参与，见 src/research_check.py）
    s_research = "SKIPPED"
    rc = None
    # ★ 只要有 claim 就必须跑 C研：模型一条也没答上（典型是离线缓存未命中）时，
    #   这批引用要作为「待人工复核」留痕，而不是整批消失、让读者以为已经被核查过。
    if claims_path and (claim_matches or claim_unmatched):
        rc = run_research_check(claims_path, claim_matches or [], check_records, run_id,
                                unmatched=claim_unmatched)
        s_research = "PASS={}/FAIL={}/REVIEW={}".format(
            sum(1 for r in rc["results"] if r["status"] == "PASS"),
            sum(1 for r in rc["results"] if r["status"] == "FAIL"),
            len(rc["review"]))
    # 2c. C研 语义审计（RC06，见 src/semantic_audit.py）：把确定性规则覆盖不到的语义型
    #     问题（同比方向、期间语义、单位量级、研报内部预测表勾稽、单季混用、枚举消歧）
    #     交给模型不限类型地审一遍。结论一律落「待人工复核」，不升级为异常、不改写数字。
    s_audit = "SKIPPED"
    if claims_path and rc is not None:
        from src.semantic_audit import run_semantic_audit
        _findings, s_audit = run_semantic_audit(
            claims_path, rc, run_id, offline, fields=_claim_fields(check_records),
            api_key=api_key)
    # 3. 规则核查（C内/C间/C期/C附，见 src/rules_engine.py）
    rr = run_rules_engine(records, run_id, offline)

    # 4. 异常原因解释：模型在程序给出的候选原因中做选择，程序校验其合规性
    s_explain = "SKIPPED"
    rule_res = (rr or {}).get("results") or []
    rc_res = (rc or {}).get("results") or []
    if rule_res or rc_res:
        from src.explain import explain_anomalies
        # ★ 只写 explanations.json，**不回写 results.json / research_claims.json**。
        #   模型措辞与置信度在多次真实调用间会浮动（实测同一输入 confidence 0.80/0.85），
        #   混进结果文件会让"结果可机器比对、逐字节复现"失效。
        #   结果文件保持纯确定性；模型输出单独存放，由报告/网页按编号关联展示。
        explain_anomalies(rule_res, run_id, offline, research_results=rc_res,
                          api_key=api_key)
        s_explain = explain_summary(run_id)

    # 5. 研报式评价：程序把确定数字喂给模型写分析评价（数字白名单校验，可离线降级）
    s_comment = run_commentary(records, rule_res, rc_res, (rr or {}).get("csi"),
                               run_id, offline, api_key=api_key)

    manifest["outputs"].update({"match_item_status": s_item,
                                "match_claim_status": s_claim,
                                "research_check": s_research,
                                "semantic_audit": s_audit,
                                "explain": s_explain,
                                "commentary": s_comment})
    save_manifest(run_id, manifest)
    # ★ 落盘本轮源码指纹：报告/网页据此判断产物是否由当前代码产出。
    #   服务进程常驻内存，改了代码没重启时会继续用旧模块算，产物与代码不一致——
    #   打上指纹后，读取方才能发现并（见 src/refresh.py）自动重算确定性部分。
    from src.codefingerprint import write_fingerprint
    write_fingerprint(run_id)
    return {"run_id": run_id, "match_item": s_item, "match_claim": s_claim,
            "research_check": s_research, "semantic_audit": s_audit,
            "explain": s_explain, "commentary": s_comment}
