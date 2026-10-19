---
name: explain_anomaly
description: 在程序给出的候选原因中为一条异常做"选择题"，并给出可溯源的原文证据。不自行归因、不产金额。
---

# Skill：explain_anomaly（异常原因解释）

## 定位

规则引擎判定 FAIL/REVIEW 后，由本 Skill 从**冻结候选集**中选一个原因并写一段解释。
归因不是自由文本，而是受限枚举——避免模型臆断。

## 代码落点

| 环节 | 文件 |
|---|---|
| 入参构造 | `src/inputs.py::build_explain_anomaly` |
| 输出 schema | `src/schemas.py::ExplainOut`（含 `Evidence`） |
| 提示词 | `prompts/explain_anomaly.txt` |
| 调用与校验 | `src/explain.py::explain_anomalies` |

## 输入

| 字段 | 类型 | 说明 |
|---|---|---|
| `task` | string | 固定 `"explain_anomaly"` |
| `rule_id` | string | 规则编号 |
| `diff_desc` | string | 程序生成的差异自然语言描述（不含明细数值） |
| `chunk_id` | string | 只能引用本次输入中的 chunk |
| `quote` | string | 原文片段，需真实存在 |
| `candidates` | string[] | 冻结候选：`UNIT_SCALE / PERIOD_MISMATCH / SCOPE_MIXED / VALUE_ERROR / SIGN_ERROR / UNCERTAIN` |

## 输出（经 `ExplainOut` 校验）

```json
{"ok": true, "cause": "UNIT_SCALE", "explanation": "…", "confidence": 0.8,
 "evidence": {"chunk_id": "p042_t1", "quote": "单位：万元"}}
```

- `cause` 只能取自 `candidates`（`src/tasks.ALLOWED_CAUSES`）。
- 本任务**含 evidence 且必须校验**：`chunk_id` 必须存在于本次输入，
  `quote` 必须真实出现在该 chunk 文本中（`src/validators.check_evidence`）。

## 校验层

1. Pydantic 结构；2. 枚举白名单（cause）；3. 证据真实性（chunk_id + quote）。
