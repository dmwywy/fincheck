# tools/make_mock.py
"""生成离线 mock 缓存，供无 API Key 的契约联调。

原理：src/llm.py 的缓存键 = sha256(提示词内容 + 模型标识 + 推理参数 + 输入数据)，
**不含凭据与时间戳**。因此只要按同样的输入构造出同样的键，把 mock 响应写进
runs/<run_id>/llm_cache/<键>.json，离线模式就能命中它并走完全部下游校验。

本工具复用 pipeline 的构造逻辑，保证 payload 与真实运行时逐字节一致；
并复现 pipeline 的分批策略，为每个批次各生成一个缓存文件。

用法：
    # 1) 查看会请求哪些批次，并生成空白骨架
    python tools/make_mock.py --task match_claim --run-id offline_demo --emit-template

    # 2) 用自己写的响应覆盖某个批次（响应须符合 src/schemas.py 的结构）
    python tools/make_mock.py --task match_claim --run-id offline_demo --response my.json

    # 3) 离线验证
    python -m fincheck run --config configs/demo.yaml --run-id offline_demo --offline
"""
import argparse
import json
import sys
from pathlib import Path

# 从 tools/ 目录直接运行时要能 import src
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.inputs import MAX_ITEMS, build_match_claim, build_match_item
from src.llm import cache_key
from src.pipeline import (CLAIM_BATCH, _claim_fields, _standard_items,
                          load_config, load_structured)
from src.tasks import load_prompt

ROOT = Path(__file__).resolve().parents[1]


def build_payloads(task: str, cfg: dict) -> list[dict]:
    """复现 pipeline 的分批，返回该任务实际会发出的全部 payload。"""
    records = load_structured(cfg.get("paths", {}).get("structured", "data/structured"))

    if task == "match_item":
        headers = sorted({r["item_raw"] for r in records if r.get("item_raw")})
        standard = _standard_items()
        # 与 pipeline.run_match_item 一样按 MAX_ITEMS 分批：规则库扩容后
        # 一份年报的科目名已超过 40 个，不分批会直接触发断言（实测踩过）。
        return [build_match_item(headers[i:i + MAX_ITEMS], standard)
                for i in range(0, len(headers), MAX_ITEMS)]

    if task == "match_claim":
        path = cfg.get("run", {}).get("materials", {}).get("report_claims", "")
        if not path:
            raise SystemExit("配置里没有 report_claims，无法构造 match_claim 的 payload")
        claims = json.loads((ROOT / path).read_text(encoding="utf-8"))
        fields = _claim_fields(records)
        return [build_match_claim(
            [{"claim_id": c["claim_id"], "sentence": c["sentence"]}
             for c in claims[i:i + CLAIM_BATCH]], fields)
            for i in range(0, len(claims), CLAIM_BATCH)]

    raise SystemExit(f"暂不支持生成 {task} 的 mock；可按同样方式扩展本工具")


def template_for(task: str, payload: dict) -> dict:
    """给出符合 schema 的最小骨架，交给人填。刻意不编造语义结论。"""
    if task == "match_item":
        return {"ok": True,
                "items": [{"raw_header": h, "canonical_item": "", "confidence": 0.0}
                          for h in payload.get("headers", [])]}
    if task == "match_claim":
        return {"ok": True,
                "matches": [{"claim_id": c["claim_id"], "item_code": "", "period": "",
                             "confidence": 0.0, "reason": "mock 骨架，待填写"}
                            for c in payload.get("claims", [])]}
    return {"ok": True}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True,
                    choices=["match_item", "match_claim"])
    ap.add_argument("--config", default="configs/demo.yaml")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--emit-template", action="store_true",
                    help="写出可直接编辑的骨架响应")
    ap.add_argument("--response", help="用该 JSON 文件作为响应，覆盖所有批次")
    args = ap.parse_args()

    cfg = load_config(args.config)
    prompt_text, meta = load_prompt(args.task)
    payloads = build_payloads(args.task, cfg)

    override = None
    if args.response:
        override = json.loads(Path(args.response).read_text(encoding="utf-8"))

    out_dir = ROOT / "runs" / args.run_id / "llm_cache"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"任务 {args.task}｜提示词 sha256 {meta['sha256'][:16]}…｜共 {len(payloads)} 个批次")
    for i, payload in enumerate(payloads):
        key = cache_key(prompt_text, payload)
        body = override if override is not None else template_for(args.task, payload)
        path = out_dir / f"{key}.json"
        if args.emit_template or override is not None:
            path.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True),
                            encoding="utf-8")
            state = "已写入" if override is not None else "已写骨架"
        else:
            state = "存在" if path.exists() else "缺失"
        size = len(json.dumps(payload, ensure_ascii=False))
        print(f"  批次{i + 1}: cache_key={key[:16]}… payload={size}B -> {state}")

    if not (args.emit_template or override):
        print("\n未指定动作。加 --emit-template 生成骨架，或 --response <file> 写入响应。")
    else:
        print(f"\n下一步：python -m fincheck run --config {args.config} "
              f"--run-id {args.run_id} --offline")


if __name__ == "__main__":
    main()
