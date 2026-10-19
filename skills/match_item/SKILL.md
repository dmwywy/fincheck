---
name: match_item
description: 把财报原文科目名归一化为标准科目编号（rules/fields.yaml 的 code）。只做语义映射，不产金额、不产页码。
---

# Skill：match_item（科目归一化）

## 定位

本 Skill 是项目 4 个受注册模型任务之一。运行时由 `src.tasks.call_task` 承载，
本文件是它的**声明式定义**（能力边界 + 输入输出契约 + 代码落点），供评审核对模块齐套。
业务逻辑不依赖本文件；删除本文件不影响运行，仅代表该 Skill 的"声明"缺失。

## 代码落点

| 环节 | 文件 |
|---|---|
| 入参构造 | `src/inputs.py::build_match_item` |
| 输出 schema | `src/schemas.py::MatchItemOut` |
| 提示词 | `prompts/match_item.txt` |
| 调用与校验 | `src/tasks.py::call_task` |
| 编排 | `src/pipeline.py::run_match_item` |

## 输入

| 字段 | 类型 | 说明 |
|---|---|---|
| `task` | string | 固定 `"match_item"` |
| `headers` | string[] | 财报原文科目名；单批 ≤ 40 条（`src/inputs.MAX_ITEMS`） |
| `standard_items` | object[] | `[{"code": "...", "name": "..."}]`，取自 `rules/fields.yaml` |

## 输出（经 `MatchItemOut` 校验）

```json
{"ok": true,
 "items": [{"raw_header": "营业总收入", "canonical_item": "TOTAL_REVENUE", "confidence": 0.7}]}
```

- `canonical_item` 必须取自 `standard_items`；清单外的值由程序判为无效。
- 本任务**不含 evidence**：科目位置（page/chunk_id/raw_text）由
  `src/pipeline.py::_attach_evidence` 从解析记录确定性回填。
- 无法确定时 `canonical_item` 填空串、`confidence` ≤ 0.3。

## 校验层

1. Pydantic 结构（`MatchItemOut`）；
2. 枚举白名单（`item_codes` 来自 `fields.yaml`，见 `src/tasks.py::_post_checks`）；
3. 科目名包含性校验（回填证据时 `raw in chunk`）。
任一失败回传错误、修复重试一次。
