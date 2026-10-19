---
name: audit_claims
description: 对研报原文做不限错误类型的语义审计，指出确定性规则覆盖不到的语义型问题（同比方向、期间语义、单位量级、研报内部预测表勾稽等），结论一律转人工复核。
---

# Skill：audit_claims（研报语义审计）

## 定位

C研 维度的**语义补漏层**（规则号 `RC06@v1.0`）。确定性规则只能判"数值比得上/比不上"，
对同比方向写反、期间语义错配、单季与全年混用、研报内部预测表勾稽这类问题天然失明——
这些靠加正则永远加不完。本 Skill 把这类判断交给模型：**不限错误类型**地审一遍，由它指出问题。

## 代码落点

| 环节 | 文件 |
|---|---|
| 入参构造 | `src/inputs.py::build_audit_claims` |
| 输出 schema | `src/schemas.py::AuditOut` / `AuditFinding` |
| 提示词 | `prompts/audit_claims.txt` |
| 调用与合并 | `src/semantic_audit.py::run_semantic_audit` |
| 编排 | `src/pipeline.py`（步骤 2c） |
| 展示 | `src/report.py`（PDF/HTML/Excel）、`app.py`（异常清单页） |

## 输入

| 字段 | 类型 | 说明 |
|---|---|---|
| `task` | string | 固定 `"audit_claims"` |
| `claims` | object[] | `[{"claim_id","sentence","page","para_no"}]`，单批 20 条 |
| `program_findings` | object[] | 程序已给出的结论，仅供模型**避免重复报同一处** |
| `fields` | object[] | `[{"item_code","period"}]`——只给科目与期间，不给金额 |

## 输出（经 `AuditOut` 校验）

```json
{"ok": true,
 "findings": [{"claim_id": "r2025_p04_002", "problem_type": "同比方向写反",
               "cause": "SIGN_ERROR", "severity": "HIGH",
               "evidence_quote": "同比+1.2%", "expected_value": "应为 -1.2%",
               "reason": "…", "confidence": 0.8}]}
```

## 校验层与安全边界

1. Pydantic 结构；2. `cause` 落在 `ALLOWED_CAUSES` 内；
3. `claim_id` 必须来自本次输入；4. `evidence_quote` 必须**逐字**存在于该 claim 的原句中
   （编造证据一律拦下并触发一次修复重试）。

★ **安全边界**：模型结论一律落「待人工复核（模型语义审计提示）」，**绝不升级为异常、
绝不改动任何数值**。程序判定的异常可复现，模型判断不可复现，两者不混在同一计数口径里。
程序已判定的 `(claim_id, cause)` 保留，模型只做补充；命中同一处的不重复报。
离线 / 缓存未命中时整层优雅降级为空，纯规则照常出报告。
