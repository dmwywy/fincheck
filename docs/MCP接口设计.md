# MCP 接口设计与实现

> 服务代码位于 [`agent/mcp_server/`](../agent/mcp_server/)（`server.py` + `__main__.py`），
> 启动 `python -m mcp_server`（stdio），离线自检 `python -m mcp_server --selftest`；
> 客户端接入配置样例见代码仓库根 [`.mcp.json`](../agent/.mcp.json)。
>
> 实现选择：用**标准库**实现 stdio JSON-RPC 2.0，**零新增第三方依赖**（评审环境无需安装 `mcp` SDK），
> 目录名用 `mcp_server/` 而非 `mcp/`，避免同名本地包遮蔽已安装的官方 SDK。

## 1. 设计原则

- **零侵入**：MCP 层只做"薄封装"，不修改 `src/` 中的业务流程代码。
  现有 CLI（`python -m fincheck …`）与 Web（`streamlit run app.py`）已是完整入口，
  MCP 只是把它们按工具（tool）暴露给 MCP 客户端。
- **可复现**：MCP 工具的输出与 CLI 产物逐字节一致（都落在 `runs/<run-id>/`）。

## 2. 工具清单

| MCP tool | 对应现有入口 | 关键入参 | 产物 |
|---|---|---|---|
| `fincheck_ingest` | `python -m fincheck ingest --from <目录> --run-id <id> [--offline]` | `from_dir`, `run_id`, `offline`, `report_period` | `runs/<id>/ingested/*.json` |
| `fincheck_run` | `python -m fincheck run --config configs/demo.yaml --run-id <id> [--offline]` | `config`, `run_id`, `offline` | `runs/<id>/rules/*.json`、`llm/*.json`、`manifest.json` |
| `fincheck_report` | `python -m fincheck report --run-id <id>` | `run_id`, `formats` | `runs/<id>/report.{pdf,xlsx,html}` |
| `calculate_ratios` | `src/financial_analysis.py::analyze` | `records` | 盈利能力/费用率/趋势/结构四组指标 JSON（纯计算，不落盘） |
| `check_consistency` | `src/rules_engine.py::run_rules` | `records`, `run_id` | 规则结果 + CSI 评分 JSON |

## 3. 资源（resources）

| MCP resource URI | 内容 |
|---|---|
| `fincheck://runs` | 历史运行编号列表 |
| `fincheck://runs/{run_id}/manifest.json` | 运行清单（提示词哈希、调用统计） |
| `fincheck://runs/{run_id}/rules/csi.json` | 一致性指数与维度明细 |

## 4. 与 Skill 的关系

`skills/` 下共 **9** 个 `SKILL.md`：
- **模型侧任务**（5）：`match_item` / `match_claim` / `explain_anomaly` / `gen_commentary` /
  `audit_claims`，与 `src.schemas.SCHEMA_BY_TASK` 一一对应；
- **确定性能力**（4）：`financial-ratio-calculator`（财务比率计算）/
  `consistency-checker`（一致性核查，封装 27 条规则）/ `field-extractor`（字段提取）/
  `report-generator`（报告生成），对应 MCP 工具或 CLI/Web 入口。

MCP 工具声明的是**流程侧入口**，Skill 声明的是**能力边界**；二者通过
`runs/<run-id>/` 的落盘产物衔接，互不依赖。

## 5. 实现说明

- **协议方法**：`initialize` / `notifications/initialized` / `tools/list` / `tools/call` /
  `resources/list` / `resources/read` / `ping`（stdio 逐行 JSON-RPC 2.0）。
- **工具实现**：`mcp_server/server.py` 的 `TOOLS` 注册表，逐个薄封装 `src/` 既有函数——
  `fincheck_ingest`→`src.ingest.ingest`、`fincheck_run`→`src.pipeline.run`、
  `fincheck_report`→`src.report.build_all`、`calculate_ratios`→`src.financial_analysis.analyze`、
  `check_consistency`→`src.rules_engine.run_rules`；**不修改任何业务流程代码**。
- **协议优先**：stdout 只写 JSON-RPC 消息，工具执行期间的一切库输出重定向到 stderr，避免污染协议流。
- **自检**：`python -m mcp_server --selftest` 对 `initialize` / `tools/list` / 一次
  `calculate_ratios` 调用 / `resources/read` 四个往返做断言，不联网、不调模型。
