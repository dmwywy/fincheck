# 模型接口契约

## 1. 模型标识 ★ 最重要的一条

| 项 | 值 |
|---|---|
| 产品/版本名（对外表述） | **DeepSeek-V4.1-Flash** |
| **请求体 `model` 字段的值** | **`deepseek-flash`** |

官方文档原文（[首次调用 API](https://api-docs.deepseek.com/zh-cn/)）：

> `model`：`deepseek-flash`(1)、`deepseek-v4-pro`
> (1) **模型名请使用 `deepseek-flash`。旧模型名 `deepseek-v4-flash`、
> `deepseek-v4-flash-vision-exp` 仍可调用，但对应模型已下线，请求将由
> DeepSeek-V4.1-Flash 模型提供服务，并按 Flash 价格计费。**

**结论**：产品上确实确定为 DeepSeek-V4.1-Flash；但**请求里绝不能写 `DeepSeek-V4.1-Flash`**，
必须写 `deepseek-flash`。这是"产品代际名"与"可调用模型标识"的区别，也是运行记录能否确定
实际调用对象的前提。仓库已内置白名单校验（`src/llm.py`），写错在启动瞬间报错，不发出请求。

白名单当前值：`{"deepseek-flash", "deepseek-v4-pro"}`，默认 `deepseek-flash`。

---

## 2. 正式 Endpoint

| 项 | 值 |
|---|---|
| HTTP 方法 | `POST` |
| 完整路径 | `https://api.deepseek.com/chat/completions` |
| OpenAI SDK `base_url` | `https://api.deepseek.com`（SDK 自动补 `/chat/completions`） |
| Content-Type | `application/json` |
| 协议 | OpenAI 兼容（亦提供 `https://api.deepseek.com/anthropic` 的 Anthropic 兼容入口） |

来源：官方文档 [Chat Completions API](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)。

仓库实现走 OpenAI SDK（`openai==3.19.2`），不手工拼 URL。

---

## 3. 认证

| 项 | 值 |
|---|---|
| HTTP 头 | `Authorization: Bearer ${DEEPSEEK_API_KEY}` |
| **环境变量名** | **`DEEPSEEK_API_KEY`** |
| 凭据来源 | 仅 `.env` 或系统环境变量；`.env` 已在 `.gitignore` |
| 日志/报告/manifest | 一律掩码，形如 `sk-abc***xyz` |
| 是否进缓存键 | **否**（因此换机器、换 Key 仍命中同一缓存） |

诊断命令：`python check_key.py`（只打印掩码与来源，不输出明文）。

---

## 4. 请求 JSON（实测值）

```json
{
  "model": "deepseek-flash",
  "messages": [
    {"role": "system", "content": "<prompts/<task>.txt 的全文>"},
    {"role": "user",   "content": "<src/inputs.py 构造的 payload，JSON 字符串>"}
  ],
  "temperature": 0,
  "top_p": 1,
  "max_tokens": 8000,
  "stream": false,
  "response_format": {"type": "json_object"},
  "user_id": "<运行操作者代号>",
  "thinking": {"type": "disabled"}
}
```

| 参数 | 值 | 说明 |
|---|---|---|
| `temperature` | `0` | 固定；参与缓存键 |
| `top_p` | `1` | 固定；参与缓存键 |
| `max_tokens` | `8000` | 见 §9；参与缓存键 |
| `stream` | `false` | 本项目不使用流式 |
| `response_format` | `{"type":"json_object"}` | 强制 JSON，见 §9 |
| `user_id` | 操作者代号 | 按官方要求须匹配 `[a-zA-Z0-9\-_]+`、长度 ≤512；**不写真实姓名** |
| `thinking` | `{"type":"disabled"}` | **非 OpenAI 标准字段，须经 `extra_body` 传**；见 §9.3 |

**唯一出站入口**：`src/llm.py` 的 `call()`。其余模块不得直接调用模型接口。

两条输出约定（`match_item` / `match_claim`）：

- 期间编码：时期写 `2024`，时点写 `2024-12-31`，**不使用 `FY2024`**。
- 输出**不含 `evidence` 字段**：页码/定位/原文由程序从解析结果确定性回填，模型不产出位置信息。

---

## 5. 响应 JSON（实测 envelope）

以下为一次真实调用的完整响应（仅 `content` 截断）：

```json
{
  "id": "chat-...",
  "object": "chat.completion",
  "created": 1791202206,
  "model": "deepseek-flash",
  "choices": [
    {
      "index": 0,
      "finish_reason": "stop",
      "logprobs": null,
      "message": {
        "role": "assistant",
        "content": "{\"ok\":true}",
        "reasoning_content": "We need answer user asks in Chinese: ...",
        "refusal": null,
        "annotations": null,
        "audio": null,
        "function_call": null,
        "tool_calls": null
      }
    }
  ],
  "usage": {
    "prompt_tokens": 65,
    "completion_tokens": 56,
    "total_tokens": 121,
    "completion_tokens_details": {
      "reasoning_tokens": 50
    },
    "prompt_tokens_details": {"cached_tokens": 0},
    "prompt_cache_hit_tokens": 0,
    "prompt_cache_miss_tokens": 65
  },
  "service_tier": null,
  "system_fingerprint": "aeb56401ca74e127821c4f9126dcb669"
}
```

**程序只消费两处**：

1. `choices[0].message.content` → `json.loads()` 后交给 `src/schemas.py` 校验；
2. `choices[0].finish_reason` 与 `usage.completion_tokens` → 仅用于诊断日志。

`reasoning_content` 和 `usage` 的其余字段**不参与业务逻辑**，只用于排查。
`finish_reason` 取值：`stop`（正常）/ `length`（被 `max_tokens` 截断，是故障信号）。

---

## 6. 错误格式（实测 + 官方错误码）

### 6.1 错误响应体（实测 401）

```json
{
  "error": {
    "message": "Authentication Fails, Your api key: ****robe is invalid (request_id: 5e3e297c-...-fb2011d611c9)",
    "type": "authentication_error",
    "param": null,
    "code": "invalid_request_error"
  }
}
```

- **HTTP 状态码**携带真正的错误类别；
- `error.code` **不是** HTTP 码（400/401 都可能是 `invalid_request_error`），**判定必须用 HTTP 状态码**；
- `error.message` 内含 `request_id`，报障时提供给服务商。

OpenAI SDK 会抛对应异常，`e.status_code` 取状态码、`e.body` 取上面的 dict。
`src/errors.py` 的 `classify(e)` 据此分类。

### 6.2 官方错误码（与 `src/errors.py` 完全一致）

| 码 | 含义 | 是否重试 | 程序动作 |
|---|---|---|---|
| 400 | 请求体格式错误 | **否** | 停止，抛 `ProviderError`，提示检查模型名与参数 |
| 401 | 认证失败 | **否** | 停止，提示检查 Key |
| 402 | 余额不足 | **否** | **降级为离线模式**，不中断 |
| 422 | 参数错误 | **否** | 停止，提示检查 model / max_tokens |
| 429 | 请求速率达上限 | **是** | 退避重试，最多 3 次 |
| 500 | 服务器故障 | **是** | 同上 |
| 503 | 服务器繁忙 | **是** | 同上 |
| — | 网络超时/连接失败 | **是** | 同上 |

来源：官方 [错误码](https://api-docs.deepseek.com/zh-cn/quick_start/error_codes)。

### 6.3 程序自定义的失败状态（非 HTTP 错误）

| 状态 | 含义 | 处理 |
|---|---|---|
| `OUTPUT_EMPTY` | 响应有 HTTP 200 但 `content` 为空（推理烧光额度） | **不重试**，返回失败 |
| `SCHEMA_INVALID` | 输出非合法 JSON，重试后仍失败 | 该条记 `REVIEW`，不中断 |
| `NO_CACHE_OFFLINE` | 离线模式且无对应缓存 | 该条记 `REVIEW`，不中断 |
| `RETRY_EXHAUSTED:<码>` | 可重试错误重试 3 次仍失败 | 该条记 `REVIEW`，不中断 |

**原则**：任何单条失败都不中断整体流程，一律转"需人工复核"。

---

## 7. 超时与重试

| 项 | 现状值 | 位置 |
|---|---|---|
| 请求超时 | **90 秒** | `src/llm.py` 的 `timeout=90` |
| 最大重试次数 | **3 次** | `max_retry=3` |
| 退避策略 | 指数退避 `2^i` 秒 + `0~0.4s` 随机抖动 | `time.sleep(2 ** i + random.uniform(0, 0.4))` |
| 并发 | 串行（单线程顺序调用） | `configs/model.yaml` 的 `concurrency: 2` 为**规划值，尚未实现** |

---

## 8. 并发与限流（官方）

| 项 | 值 |
|---|---|
| `deepseek-flash` 并发限制 | **2500** |
| `deepseek-v4-pro` 并发限制 | 500 |
| `user_id` 粒度限制 | 提升配额后按 `user_id` 分别限制，Flash 仍为 2500 |
| `user_id` 格式 | 正则 `[a-zA-Z0-9\-_]+`，长度 ≤512 |
| 超限表现 | HTTP 429 |

来源：官方 [限速与隔离](https://api-docs.deepseek.com/zh-cn/quick_start/rate_limit)。

本项目的调用量（单次运行 3~20 次）远低于限额，**无需申请扩容**。

---

## 9. JSON 输出约束

### 9.1 强制 JSON

请求带 `response_format={"type":"json_object"}`。官方要求提示词中必须出现
**"json" 字样并给出输出样例**——仓库的 5 个提示词均满足，并有测试守护
（`test_tasks.py::test_prompt_files_naming` 断言每个提示词含 `json`）。

### 9.2 三层校验（`src/tasks.py::call_task`）

| 层 | 内容 | 失败处理 |
|---|---|---|
| 1. 结构 | Pydantic 校验 `src/schemas.py` 的 5 个模型 | 修复重试 1 次 |
| 2. 枚举白名单 | `canonical_item` / `item_code` / `cause` 只能取约定集合内的值 | 同上 |
| 3. 证据真实性 | `chunk_id` 必须来自本次输入；`quote` 必须真实存在于对应文本 | 同上 |

失败重试仍不过 → 该条记 `REVIEW`，不中断。

### 9.3 ★ 必须关闭深度思考（`thinking`）

`deepseek-flash` **默认开启深度思考**，`reasoning_content` 的 token 计入
`completion_tokens`，**与正文共享 `max_tokens`**。实测同一批输入的对照：

| `thinking` | `finish_reason` | `completion_tokens` | `reasoning_tokens` | 正文 |
|---|---|---|---|---|
| `enabled`（默认） | `length` | **8000（全部烧在推理）** | 8000 | **空字符串** |
| `disabled` | `stop` | **242** | — | **668 字符，正常** |

**相差 33 倍，且默认档位直接导致契约失败**（`content=''` → `json.loads('')` 抛错）。

本项目的模型任务以"**带候选清单的受限分类/映射**"为主（选科目、选期间、选原因），
不需要长链推理，因此所有任务统一固定为：

```json
"thinking": {"type": "disabled"}
```

该参数**参与缓存键**（它改变输出）。`thinking` 不是 OpenAI 标准字段，须通过
SDK 的 `extra_body` 传递，不能作为顶层参数。

---

## 10. 最小可用请求 / 响应示例（基于本仓库真实数据）

### 10.1 请求

任务 `match_claim`，输入为 `data/demo/研报claims_2024.json` 的第 1 条。

**HTTP 形态：**

```http
POST /chat/completions HTTP/1.1
Host: api.deepseek.com
Content-Type: application/json
Authorization: Bearer ${DEEPSEEK_API_KEY}
```

```json
{
  "model": "deepseek-flash",
  "messages": [
    {"role": "system", "content": "你是研报引用核查助手。请只输出 json 格式，……"},
    {"role": "user", "content": "{\"task\":\"match_claim\",\"claims\":[{\"claim_id\":\"r2024_p02_001\",\"sentence\":\"2024 年稳健收官，2025 年目标收入增长9%\"}],\"fields\":[{\"item_code\":\"TOTAL_REVENUE\",\"period\":\"2024\"},{\"item_code\":\"REVENUE\",\"period\":\"2024\"}]}"}
  ],
  "temperature": 0,
  "top_p": 1,
  "max_tokens": 8000,
  "stream": false,
  "response_format": {"type": "json_object"},
  "user_id": "member_e",
  "thinking": {"type": "disabled"}
}
```

（`fields` 中**只给科目与期间，不给金额**——防止模型越界做数值判断。）

### 10.2 响应

```json
{
  "id": "chat-0f3c...",
  "object": "chat.completion",
  "created": 1791202206,
  "model": "deepseek-flash",
  "choices": [{
    "index": 0,
    "finish_reason": "stop",
    "message": {
      "role": "assistant",
      "reasoning_content": "",
      "content": "{\"ok\":true,\"matches\":[{\"claim_id\":\"r2024_p02_001\",\"item_code\":\"TOTAL_REVENUE\",\"period\":\"2025\",\"confidence\":0.9,\"reason\":\"句子指向2025年收入增长目标\"}]}"
    }
  }],
  "usage": {"prompt_tokens": 1203, "completion_tokens": 78, "total_tokens": 1281}
}
```

### 10.3 缓存文件（离线重放的落盘形态）

成功调用后，`src/llm.py` 把 **`content` 解析后的对象**（不是整个 envelope）写入：

```
runs/<run_id>/llm_cache/<cache_key>.json
```

`cache_key = sha256(提示词内容 + 模型标识 + 推理参数 + 输入数据)`，**不含凭据与时间戳**。

---

## 11. 离线 mock 联调流程

```bash
# 1) 生成骨架响应（为每个批次各生成一个缓存文件）
python tools/make_mock.py --task match_item  --run-id offline_demo --emit-template
python tools/make_mock.py --task match_claim --run-id offline_demo --emit-template

# 2) 用符合 src/schemas.py 的响应覆盖（可选；不覆盖则用骨架）
python tools/make_mock.py --task match_claim --run-id offline_demo --response my.json

# 3) 离线跑通 —— 不需要网络、不需要 API Key
python -m fincheck run --config configs/demo.yaml --run-id offline_demo --offline
```

**实测结果**：

```
调用状态: {'CACHE_HIT': 3}          ← 3 次全部命中 mock，零联网
claim status: CACHE_HIT | matches: 39
item status:  CACHE_HIT | mappings: 22
```

### 重要约束

`cache_key` 含**提示词内容 + 推理参数 + payload**，因此下列任一改动都会使 mock 失效
（表现为 `NO_CACHE_OFFLINE`）：

- 改 `prompts/<task>.txt` 任意字符；
- 改 `temperature` / `top_p` / `max_tokens` / `thinking`；
- 改 `src/inputs.py` 的 payload 结构；
- 改批大小（`CLAIM_BATCH`）或输入文件。

改动后**必须重新生成 mock**。同输入 → 同键 → 同结果，是"结果可复现"的技术基础。

---

## 12. 附：提示词与参数指纹（缓存键组成）

| 项 | 当前值 |
|---|---|
| 提示词文件 | `prompts/{match_item,match_claim,explain_anomaly,gen_commentary,audit_claims}.txt`（5 个） |
| 提示词内容哈希 | 每次运行写入 `runs/<run_id>/manifest.json` 的 `prompts[].sha256` |
| 模型标识 | `deepseek-flash` |
| 推理参数 | `temperature=0, top_p=1, max_tokens=8000, stream=false, thinking={"type":"disabled"}` |
| 缓存键 | `sha256(提示词内容 + 模型标识 + 推理参数 + 输入数据)`，不含凭据、时间戳、机器名 |
