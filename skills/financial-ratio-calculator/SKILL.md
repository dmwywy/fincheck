---
name: financial-ratio-calculator
description: 财务比率确定性计算：由结构化字段算出盈利能力、费用率、趋势、结构四组指标，纯程序计算、无大模型依赖、结果可复现。
---

# Skill：financial-ratio-calculator（财务比率计算）

## 定位

本 Skill 是项目的**确定性计算能力**（非模型任务）：把 B 阶段核定的结构化记录
算成四组分析指标，供报告「财务比率分析」章节与 `gen_commentary` 上下文消费。
遵循铁律：只做加/减/乘/除，不引入任何外部数据（股价、股本、一致预期一律不用）；
每条结果都带 `basis`（口径说明）；输入缺失**不抛异常**，对应指标直接缺席并在
`warnings` 写明原因。删除本文件不影响运行，仅代表该 Skill 的"声明"缺失。

## 代码落点

| 环节 | 文件 |
|---|---|
| 计算实现 | `src/financial_analysis.py::analyze` |
| 分组归类 | `src/financial_analysis.py::group_metrics` |
| 报告渲染 | `src/report.py::_analysis_groups` |
| 提示词上下文 | `src/pipeline.py::run_commentary` → `src/inputs.py::build_gen_commentary` |

## 输入 schema

| 字段 | 类型 | 说明 |
|---|---|---|
| `records` | object[] | 结构化字段记录 `[{item_code, period, value, ...}]`；`period` 为 `YYYY`（时期）或 `YYYY-MM-DD`（时点） |

## 输出 schema

```json
{"metrics": [{"group": "profitability", "code": "RATIO_GROSS", "name": "毛利率",
              "period": "2025", "value": "91.3", "unit": "%",
              "basis": "口径：毛利率＝（营业收入－营业成本）÷营业收入，以百分比列示。"}],
 "warnings": ["缺少营业收入或营业成本，未计算毛利率"]}
```

- `group` 四类：`profitability` 盈利能力（毛利率/净利率/ROE/ROIC）、
  `expense` 费用率（税金及附加率/销售费用率/管理费用率）、
  `trend` 趋势（同比增速/比率同比变动 pcts）、`structure` 结构（分产品/分渠道占比、吨价）。
- 非列表 / 空 / 无数值可解析时：返回空 `metrics` 并在 `warnings` 说明，**不抛异常**。
