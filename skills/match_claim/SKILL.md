---
name: match_claim
description: 判断研报句子在说哪个财务科目、哪个期间，用于研报引用核查。只做语义匹配，不判断数值对错、不产数值。
---

# Skill：match_claim（研报引用匹配）

## 定位

研报纠错选题的第一步：把研报 claim 句子映射到 `(item_code, period)`。
**数值对错由规则侧 `src/research_check.py` 确定性计算**，本 Skill 不做数值判断。

## 代码落点

| 环节 | 文件 |
|---|---|
| 入参构造 | `src/inputs.py::build_match_claim` |
| 输出 schema | `src/schemas.py::MatchClaimOut` |
| 提示词 | `prompts/match_claim.txt` |
| 调用与二分重试 | `src/pipeline.py::_match_claim_batched` |
| 编排 | `src/pipeline.py::run_match_claim` |

## 输入

| 字段 | 类型 | 说明 |
|---|---|---|
| `task` | string | 固定 `"match_claim"` |
| `claims` | object[] | `[{"claim_id","sentence"}]`，单批 ≤ 40 条 |
| `fields` | object[] | `[{"item_code","period"}]`——**只给科目与期间，不给金额** |

## 输出（经 `MatchClaimOut` 校验）

```json
{"ok": true,
 "matches": [{"claim_id": "r2024_p04_002", "item_code": "TOTAL_REVENUE",
              "period": "2024", "confidence": 0.8, "reason": "…"}]}
```

- `item_code` 必须取自 `fields.yaml`；`period` 与项目期间编码一致。
- 本任务**不含 evidence**：引用位置（研报文件名 + 章节 + 原句）由
  `src/pipeline.py::_attach_claim_evidence` 程序回填。
- 语义依据写在 `reason`。

## 校验层

1. Pydantic 结构；2. 枚举白名单；3. `claim_id` 存在性（回填时校验，不存在即证据无效）。
异常/漏答的 claim 一律进 `review`，不猜测。
