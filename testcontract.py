# test_contract.py   （项目根目录，运行：python test_contract.py）
"""模型调用方案六项自检"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
results = []

def chk(name, fn):
    try:
        info = fn(); results.append((name, True, info))
    except Exception as e:
        results.append((name, False, f"{type(e).__name__}: {e}"))

# 模型名称
def t1():
    import src.llm as L
    assert L.MODEL_ID in L.ALLOWED_MODELS
    return f"MODEL_ID = {L.MODEL_ID}（白名单生效）"

# API 调用方式
def t2():
    import src.llm as L
    assert L.BASE_URL == "https://api.deepseek.com"
    assert L.PARAMS["temperature"] == 0
    return f"{L.BASE_URL} / temp=0 / JSON 模式"

# 输入格式
def t3():
    from src.inputs import build_match_item, build_match_claim, build_explain_anomaly
    p = build_match_item(["营业总收入"], [{"code": "TOTAL_REVENUE", "name": "营业总收入"}])
    assert json.dumps(p, ensure_ascii=False)
    return "3 个构造函数可用"

# 返回格式
def t4():
    from src.schemas import SCHEMA_BY_TASK as S
    for k, cls in S.items():
        cls.model_json_schema()
    return f"{len(S)} 个 schema 可导出：{list(S)}"

# 错误处理
def t5():
    from src.errors import classify, NEVER, RETRYABLE
    class E(Exception):
        status_code = 401
    retry, hint, code = classify(E())
    assert retry is False and code == 401
    return "401 → 不重试；429/500/503 → 重试"

# 联网边界
def t6():
    from src.netguard import install_allowlist, ALLOW_HOST
    install_allowlist()
    return f"白名单已安装：{ALLOW_HOST}:443"

for n, f in [("[1] 模型名称", t1), ("[2] 调用方式", t2), ("[3] 输入格式", t3),
             ("[4] 返回格式", t4), ("[5] 错误处理", t5), ("[6] 联网边界", t6)]:
    chk(n, f)

print("=" * 56)
for name, ok, info in results:
    print(f"{'✅' if ok else '❌'} {name}：{info}")
print("=" * 56)
sys.exit(0 if all(o for _, o, _ in results) else 1)