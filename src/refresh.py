# src/refresh.py
"""产物新鲜度：代码改动后自动重算**确定性**产物。

场景：网页端服务进程常驻内存，改了 src/*.py 但没重启时，新的一轮仍用旧模块计算，
磁盘上的研究产物与当前代码不一致（实测：C研 的同比方向规则已修好，但报告里仍是
旧结论）。此时不能只给一句告警——**要把结论修对**，否则使用者看到的就是错的。

只重算确定性部分（C研 数值比对）。它依赖的三份输入全在磁盘上：
  runs/<id>/ingested/claims/report_claims.json  研报原文（claim 句 + 位置）
  runs/<id>/llm/claim_matches.json              模型已答的「这句话在说哪个科目/期间」
  runs/<id>/ingested/*.json                     字段记录
因此重算**不需要再调模型**，离线可复现。模型侧产物（mappings / commentary /
explanations）一律不动——它们不可离线复现，重算只会凭空造假。
"""
from __future__ import annotations

import json
from pathlib import Path

from src.codefingerprint import read_fingerprint, source_fingerprint, write_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def _read(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _recompute_research(run_id: str) -> bool:
    """用当前代码重算 C研 比对并覆盖 research_claims.json。缺件则返回 False。"""
    from src.pipeline import load_structured      # 延迟导入：避免模块级循环依赖
    from src.derived import ratio_records
    from src.research_check import run_research_check

    base = ROOT / "runs" / run_id
    claims_p = base / "ingested" / "claims" / "report_claims.json"
    if not claims_p.exists():
        return False                                  # 本次没上传研报，没有可重算的部分
    payload = _read(base / "llm" / "claim_matches.json", {}) or {}
    matches = payload.get("matches") or []
    unmatched = payload.get("review") or []
    if not matches and not unmatched:
        # 连模型匹配结果都没有（离线未缓存、或本次根本没跑 C研）：重算只会把整批
        # 变成"无匹配"，反而制造假结论。保持原样，由报告标注"本轮未完成自动比对"。
        return False
    records = load_structured(f"runs/{run_id}/ingested")
    run_research_check(str(claims_p.relative_to(ROOT)), matches,
                       records + ratio_records(records), run_id, unmatched=unmatched)
    return True


def ensure_fresh(run_id: str) -> dict:
    """读产物前调用：指纹不一致就用当前代码重算确定性产物。

    返回 {"stale": bool, "recomputed": bool}：
      - stale=True 表示磁盘产物由**非当前**源码产出，报告需给出显著告警；
      - recomputed=True 表示已用当前代码修正过结论（此时告警文案改为"已自动重算"）。

    ★ 无指纹记录的旧产物（本次改动之前跑的）同样自愈：那正是使用者投诉的场景——
      「代码明明改了，报告里还是没有那条异常」，而他打开的往往就是改动前跑的旧运行。
      只要该运行**确实做过 C研 比对**（有研报 claim + 模型匹配结果），就用当前代码重算；
      没有可重算的部分（本次没上传研报）就不动它，避免给一份本就没问题的报告扣"过期"帽子。
    """
    stored = read_fingerprint(run_id)
    if stored is not None and stored == source_fingerprint():
        return {"stale": False, "recomputed": False}
    done = _recompute_research(run_id)
    if stored is None and not done:
        return {"stale": False, "recomputed": False}
    write_fingerprint(run_id)
    return {"stale": True, "recomputed": done}
