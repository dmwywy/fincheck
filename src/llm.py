# src/llm.py
"""模型调用闸门：全项目唯一出站入口。凭据读取、缓存、重试、JSON 强制、调用记录。"""
import os, json, time, random, hashlib
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

from src.errors import classify, ProviderError
from src.logger import log_event

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

def _load_model_config() -> dict:
    """加载 configs/model.yaml —— 模型/推理参数的**声明式真源**。

    文件缺失或解析失败时返回空 dict，调用方沿用下方硬编码默认值：既让已存在的
    声明式配置真正生效（不再是与代码脱节的死文件），又保证配置缺失仍能出站。
    """
    p = ROOT / "configs" / "model.yaml"
    if not p.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:                       # noqa: BLE001 - 配置只做增强，不阻断出站
        log_event("model_config_load_failed", {"path": str(p)})
        return {}


_CFG = _load_model_config()

BASE_URL = (_CFG.get("base_url") or "https://api.deepseek.com").strip()
# 优先级：环境变量 MODEL_ID > configs/model.yaml 的 model_id > 代码默认值
MODEL_ID = (os.getenv("MODEL_ID") or _CFG.get("model_id") or "deepseek-flash").strip()
# ★ 推理参数必须全部写在这里：它们参与缓存键（见交付文档 model_adapter_contract.md §12）。
#   若把 max_tokens 单独传给接口而不放进 PARAMS，改了这个值缓存却不会失效，
#   会命中上一次的旧结果——"结果可复现"就失真了。
#   deepseek-flash 是**推理模型**：reasoning_content 与正文共享 max_tokens 预算。
#   改动此值会使既有缓存全部失效，需重跑 runs/demo_baseline/。
PARAMS = {"temperature": 0, "top_p": 1, "max_tokens": 8000, "stream": False}
PARAMS.update({k: v for k, v in (_CFG.get("params") or {}).items() if k in PARAMS})

# ★ 深度思考开关。deepseek-flash **默认开启思考**，推理 token 计入 completion_tokens
#   并与正文共享 max_tokens。实测同一批 claim：
#       thinking=enabled  → completion=8000（全部是 reasoning）、正文为空、finish_reason=length
#       thinking=disabled → completion=242、正常返回 668 字符
#   相差 33 倍。本项目的模型任务全是"带候选清单的受限分类/映射"（选科目、选期间、选原因），
#   不需要长链推理，故显式关闭。此参数必须进缓存键（它改变输出）。
THINKING = dict(_CFG.get("thinking") or {"type": "disabled"})

# ---- 模型白名单：写错在启动瞬间报错，不浪费一次请求 ----
ALLOWED_MODELS = set(_CFG.get("allowed_models") or ("deepseek-flash", "deepseek-v4-pro"))
if MODEL_ID not in ALLOWED_MODELS:
    if os.getenv("MODEL_ID"):
        _origin = "环境变量 MODEL_ID"
    elif _CFG.get("model_id"):
        _origin = "configs/model.yaml 的 model_id"
    else:
        _origin = "src/llm.py 的默认值"
    raise ValueError(
        f"模型名配置错误：来自 {_origin}，当前值 = {MODEL_ID!r}；"
        f"只允许 {sorted(ALLOWED_MODELS)}。"
        f"提示：产品名 DeepSeek-V4.1-Flash 对应的 model 值是 'deepseek-flash'。"
    )


def _client(api_key: str | None = None) -> OpenAI:
    """构造出站客户端。api_key 为空时回落到 .env / 环境变量。

    ★ 网页侧允许使用者在界面里临时填入 Key（见 app.py 的上传表单），
      它经 call → call_task → pipeline 一路透传到此处，**只用于构造客户端**：
      不写日志、不进缓存键、不进报告（缓存键不含凭据，换人换机器仍命中同一份缓存）。
    """
    key = (api_key or os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("未找到 DEEPSEEK_API_KEY：请在界面填入，"
                           "或检查项目根目录的 .env")
    return OpenAI(api_key=key, base_url=BASE_URL)


def cache_key(prompt_text: str, payload: dict) -> str:
    """缓存指纹：不含凭据、不含时间戳 → 换人换机器命中同一份缓存"""
    h = hashlib.sha256()
    eff_params = {**PARAMS, "thinking": THINKING}      # 全部推理参数参与指纹
    for p in (prompt_text, MODEL_ID, json.dumps(eff_params, sort_keys=True),
              json.dumps(payload, sort_keys=True, ensure_ascii=False)):
        h.update(p.encode("utf-8"))
    return h.hexdigest()


def call(prompt_text: str, payload: dict, run_id: str = "default",
         user_id: str = "team", task: str = "", offline: bool = False,
         max_retry: int = 3, api_key: str | None = None):
    """返回 (结果 dict 或 None, 状态字符串)。任何失败都不中断流程。"""
    ck = cache_key(prompt_text, payload)
    cache = ROOT / "runs" / run_id / "llm_cache" / f"{ck}.json"
    t0 = time.time()

    if cache.exists():                                   # 命中缓存：不联网、不花钱
        data = json.loads(cache.read_text(encoding="utf-8"))
        log_event(run_id, "llm_call", task=task, model=MODEL_ID,
                  status="CACHE_HIT", cache_key=ck[:16],
                  elapsed_ms=int((time.time() - t0) * 1000))
        return data, "CACHE_HIT"

    if offline:                                          # 离线且无缓存
        log_event(run_id, "llm_call", task=task, model=MODEL_ID,
                  status="NO_CACHE_OFFLINE", cache_key=ck[:16])
        return None, "NO_CACHE_OFFLINE"

    client = _client(api_key)
    for i in range(max_retry):
        try:
            r = client.chat.completions.create(
                model=MODEL_ID, timeout=90,
                response_format={"type": "json_object"},   # 强制 JSON 输出
                messages=[{"role": "system", "content": prompt_text},
                          {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                # thinking 非 OpenAI 标准字段，须走 extra_body；它与 user_id 一起传给服务端
                extra_body={"user_id": user_id, "thinking": THINKING},
                **PARAMS)                                  # 含 max_tokens，与缓存键一致
            content = r.choices[0].message.content
            # 推理占满额度 → 正文为空。这是参数问题，重试同一输入不会有不同结果，
            # 直接返回，避免白烧 3 次额度（每次都是满额 token）。
            if not content:
                log_event(run_id, "llm_call", task=task, model=MODEL_ID,
                          status="OUTPUT_EMPTY",
                          finish_reason=r.choices[0].finish_reason,
                          completion_tokens=getattr(r.usage, "completion_tokens", None))
                return None, "OUTPUT_EMPTY"
            data = json.loads(content)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2),
                             encoding="utf-8")
            log_event(run_id, "llm_call", task=task, model=MODEL_ID, status="API_OK",
                      cache_key=ck[:16], attempt=i + 1,
                      elapsed_ms=int((time.time() - t0) * 1000))
            return data, f"API_OK(try={i+1})"

        except json.JSONDecodeError:
            payload = {**payload, "_fix": "上次输出不是合法 json，请只输出 json，不要任何解释"}

        except Exception as e:
            retryable, hint, code = classify(e)
            if not retryable:
                log_event(run_id, "llm_call", task=task, model=MODEL_ID,
                          status="FAILED", http=code, hint=hint)
                if code == 402:                            # 余额不足 → 降级，不中断
                    return None, "QUOTA_EXCEEDED_SWITCH_OFFLINE"
                raise ProviderError(code, hint) from e
            if i == max_retry - 1:
                log_event(run_id, "llm_call", task=task, model=MODEL_ID,
                          status="RETRY_EXHAUSTED", http=code)
                return None, f"RETRY_EXHAUSTED:{code or 'NETWORK'}"
            time.sleep(2 ** i + random.uniform(0, 0.4))

    log_event(run_id, "llm_call", task=task, model=MODEL_ID, status="SCHEMA_INVALID")
    return None, "SCHEMA_INVALID"