# test.py
"""三个任务各跑一遍：验证 schema 校验路径，并生成缓存。
运行：python test.py            在线（首次真实调用，之后命中缓存）
      python test.py --offline  仅用缓存（评委复现，无需 Key、无需联网）"""
import json, sys
from pathlib import Path
from src.tasks import call_task, load_prompt
from src.manifest import new_manifest, save_manifest, sha256_text
from src.llm import MODEL_ID, PARAMS, BASE_URL
from src.inputs import (build_match_item,
                        build_match_claim, build_explain_anomaly)

RUN_ID = "20260927_test"
OFFLINE = "--offline" in sys.argv
OPERATOR = "member_e"

CASES = [
    ("match_item", build_match_item(
        ["营业总收入", "营业收入", "其中：主营业务收入"],
        [{"code": "REVENUE", "name": "营业收入"},
         {"code": "TOTAL_REVENUE", "name": "营业总收入"}])),

    ("match_claim", build_match_claim(
        [{"claim_id": "c01", "sentence": "公司2025年营业收入为120亿元，同比增长22%"}],
        [{"item_code": "REVENUE", "period": "FY2025"},
         {"item_code": "REVENUE", "period": "FY2024"}])),

    ("explain_anomaly", build_explain_anomaly(
        "RF01", "研报引用值与财报值存在数量级差异", "p042_t1", "营业收入",
        ["UNIT_SCALE", "PERIOD_MISMATCH", "SCOPE_MIXED",
         "VALUE_ERROR", "SIGN_ERROR", "UNCERTAIN"])),
]

# [1] 先建清单（3 条提示词全部登记）
prompt_metas = []
for task, _ in CASES:
    _text, meta = load_prompt(task)
    prompt_metas.append(meta)

m = new_manifest(
    run_id=RUN_ID, operator=OPERATOR,
    model_cfg={"provider": "deepseek", "base_url": BASE_URL,
               "model_id": MODEL_ID, "params": PARAMS},
    prompts=prompt_metas,
    input_files=[Path("prompts") / f"{t}.txt" for t, _ in CASES],
)

# [2] 逐个任务调用
print("=" * 62)
for task, payload in CASES:
    try:
        data, status = call_task(task, payload, run_id=RUN_ID,
                                 user_id=OPERATOR, offline=OFFLINE)
    except Exception as e:
        data, status = None, f"EXCEPTION:{type(e).__name__}: {e}"

    print(f"[{task}] 状态：{status}")
    if data is not None:
        print(json.dumps(data, ensure_ascii=False, indent=2)[:600])

    if status.startswith("API_OK"):
        m["calls"]["api_ok"] += 1
    elif status == "CACHE_HIT":
        m["calls"]["cache_hit"] += 1
    else:
        m["calls"]["failed"] += 1
    m["calls"]["total"] += 1
print("=" * 62)

# [3] 补全并落盘
m["status"] = "OK" if m["calls"]["failed"] == 0 else "PARTIAL"
save_manifest(RUN_ID, m)
print(f"manifest 已写入：runs/{RUN_ID}/manifest.json")
print(f"调用统计：{m['calls']}")
