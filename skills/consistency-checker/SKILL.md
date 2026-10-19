---
name: consistency-checker
description: 财报一致性核查：按 C内/C间/C期/C附 四维度共 27 条规则做表内勾稽、表间比对、跨期衔接与主表附注核对，并产出 CSI 量化评分（C研 研报引用维度另由 research_check 承担）。
---

# Skill：consistency-checker（一致性核查）

## 定位

本 Skill 是项目的**规则核查能力**（确定性计算，非模型任务）：只凭一份财报即可跑出
一致性结果，规则与容差全部来自 `rules/rules.yaml`，不含任何硬编码的财务判断。
覆盖四个维度共 **27 条规则**：

| 维度 | 含义 |
|---|---|
| C内 | 表内勾稽（同一张表内部的加总/平衡关系） |
| C间 | 表间一致（不同报表之间的对应关系） |
| C期 | 跨期衔接（本期期初 vs 上期期末） |
| C附 | 主表与附注（主表数 vs 附注披露数） |

第五个维度 **C研（研报引用）** 由 `src/research_check.py`（规则 RC01）负责，需额外提供研报。
删除本文件不影响运行，仅代表该 Skill 的"声明"缺失。

## 代码落点

| 环节 | 文件 |
|---|---|
| 规则表（27 条） | `rules/rules.yaml` |
| 字段字典 | `rules/fields.yaml` |
| 表达式求值与结果 | `src/rules_engine.py::safe_eval` / `run_rules` |
| CSI 评分 | `src/rules_engine.py::compute_csi` |
| C研 维度 | `src/research_check.py::run_research_check` |
| 编排 | `src/pipeline.py::run_rules_engine` |

## 输入 schema

| 字段 | 类型 | 说明 |
|---|---|---|
| `records` | object[] | 结构化字段记录 `[{item_code, period, value, ...}]` |
| `run_id` | string | 运行编号（产物落盘到 `runs/<run_id>/rules/`） |

## 输出 schema

```json
{"results": [{"rule_id": "BS01@1.0", "dimension": "C内", "status": "PASS|FAIL|REVIEW|NA",
              "period": "2025-12-31", "expr": "…", "diff": "…", "tolerance": "…",
              "severity": "HIGH", "evidence": {"file": "…", "page": 9, "raw_text": "…"}}],
 "csi": {"csi": 87.5, "grade": "B", "grade_label": "基本一致",
         "dimensions": {"C内": {"rules_applicable": 2, "pass": 1, "fail": 1, "review": 0, "na": 0,
                                "score": 0.875}},
         "na_dimensions": [], "provisional": false}}
```

- `status`：`PASS` 一致 / `FAIL` 不一致 / `REVIEW` 待人工复核 / `NA` 不适用。
- 金额一律用 `Decimal` 精确计算，不使用浮点。
