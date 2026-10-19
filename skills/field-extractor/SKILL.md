---
name: field-extractor
description: 财报字段提取：从年报/半年报 PDF 与 DOCX 中确定性抽取结构化字段（主表、附注、分部数据、产销量），每条记录带证据三要素 page/chunk_id/raw_text。
---

# Skill：field-extractor（字段提取）

## 定位

本 Skill 是项目的数据入口（确定性抽取，**不调用任何模型**）：把财报原文的表格行列
归属与数值抽取成结构化字段记录，写入 `data/structured/*.json`。
提取目标与别名由 `rules/fields.yaml` 与工具内的映射表驱动；每条记录都带证据三要素
（page / chunk_id / raw_text），供后续核查与报告溯源。删除本文件不影响运行。

## 代码落点

| 环节 | 文件 |
|---|---|
| 提取工具 | `tools/extract_fields.py`（`LABEL_ALIASES` / `SEGMENT_ITEMS` / `_emit` 等） |
| 字段字典 | `rules/fields.yaml` |
| 一致性自检 | `tools/check_fields.py` |
| 文件夹入口编排 | `src/ingest.py` |
| 结构化加载 | `src/pipeline.py::load_structured` |

## 输入 schema

| 字段 | 类型 | 说明 |
|---|---|---|
| `source` | path | 单个文件（`.pdf` / `.docx`）或其所在文件夹 |
| `year` | int | 报告年度（用于判别期间，如 `2025`） |
| `report_period` | string | `annual` 年报 / `half_year` 半年报 |

## 输出 schema

```json
[{"item_code": "REVENUE_MOUTAI", "item_raw": "茅台酒", "value": "145000000000.00",
  "unit_raw": "元", "period_type": "FY", "period": "2025", "scope": "CONSOLIDATED",
  "statement": "MDNA", "page": null, "chunk_id": "p082_t01_r03",
  "locator": "第三节 主营业务分产品情况 / PDF第82页 / 表#1 / 第3行",
  "raw_text": "茅台酒 | 145,000,000,000.00 | …"}]
```

- `scope` 固定 `CONSOLIDATED`（只在合并口径取数，母公司报表标题处切断）。
- `statement` ∈ `BS/IS/CF/EQ/NOTE/MDNA`；未披露的字段不写入（不用 `null` 或 `0` 表示缺失）。
