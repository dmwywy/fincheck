---
name: report-generator
description: 核查报告生成：把一次运行的全部产物汇总为报告模型，输出 PDF / Excel / 网页三种形式，含核查总评、财务比率分析、异常清单、CSI 量化评价、提取准确率与免责声明。
---

# Skill：report-generator（报告生成）

## 定位

本 Skill 是项目的交付出口（确定性渲染，不做任何计算与改写）：把一次运行的
全部产物（manifest / 规则结果 / CSI / 提取字段 / 财务分析 / 研报比对 / 模型评价）
汇总成报告模型，渲染成三种格式。所有数值与页码均取自程序产物。
首页必须含免责声明；报告只呈现核查结果，不作审计结论、不构成投资建议。
删除本文件不影响运行，仅代表该 Skill 的"声明"缺失。

## 代码落点

| 环节 | 文件 |
|---|---|
| 产物汇总 | `src/report.py::collect` |
| 成段结论 | `src/report.py::build_summary` / `build_research_review` |
| PDF 渲染 | `src/report.py::render_pdf`（STSong-Light CJK 字体） |
| Excel 渲染 | `src/report.py::render_xlsx` |
| 网页渲染 | `src/report.py::render_html` |
| 入口 | `src/report.py::build_all`（供 CLI 与 Web 调用） |

## 输入 schema

| 字段 | 类型 | 说明 |
|---|---|---|
| `run_id` | string | 运行编号；读取 `runs/<run_id>/` 下的产物 |
| `formats` | string[] | `pdf` / `xlsx` / `html` 的任意子集 |

## 输出 schema

```json
{"pdf": "runs/<run_id>/report.pdf",
 "xlsx": "runs/<run_id>/report.xlsx",
 "html": "runs/<run_id>/report.html"}
```

报告章节：核查总评 → 项目基本信息 → 数据提取结果（按报表分类）→ 财务比率分析 →
异常问题清单（含证据与差异）→ 研报引用异常 → 一致性量化评价（含可执行维度数统计）→
提取准确率（E）→ 风险提示与人工复核。
