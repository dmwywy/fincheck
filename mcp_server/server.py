# mcp_server/server.py
"""MCP（Model Context Protocol）服务：把本项目的核查能力以 stdio 工具形式暴露。

对齐交付文档 `MCP接口设计.md`，实现原则：
  - **零侵入**：工具只是 `src/` 既有函数的薄封装，不改动任何业务流程代码；
  - **可复现**：工具产物与 CLI 逐字节一致（都落在 `runs/<run_id>/`）；
  - **零新增依赖**：仅用标准库实现 stdio JSON-RPC 2.0，评审环境无需安装 mcp SDK；
  - **协议优先**：stdout 只写 JSON-RPC 消息；工具执行期间的一切库输出重定向到
    stderr，避免污染协议流。

启动：
    python -m mcp_server               # stdio 服务（供 MCP 客户端连接）
    python -m mcp_server --selftest    # 离线自检：不发网络请求、不调用模型
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "fincheck-mcp"
SERVER_VERSION = "1.0.0"


# ---------------------------------------------------------------- 工具实现

def _runs_dir() -> Path:
    return ROOT / "runs"


def _run_summary(run_id: str) -> dict:
    """读回落盘产物，给出一次运行的摘要（与 CLI print_summary 同源，不重算）。"""
    base = _runs_dir() / run_id
    out: dict = {"run_id": run_id, "run_dir": f"runs/{run_id}"}

    def _read(rel: str):
        try:
            return json.loads((base / rel).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    manifest = _read("manifest.json") or {}
    out["calls"] = manifest.get("calls", {})
    csi = _read("rules/csi.json")
    if csi:
        out["csi"] = csi.get("csi")
        out["grade"] = csi.get("grade")
    rc = _read("rules/research_claims.json")
    if rc:
        out["research_results"] = len(rc.get("results", []))
        out["research_review"] = len(rc.get("review", []) or [])
    rules = _read("rules/results.json")
    if rules:
        out["rule_results"] = len(rules.get("results", []))
    out["artifacts"] = sorted(
        str(p.relative_to(ROOT)).replace("\\", "/")
        for p in base.glob("*") if p.is_file())
    return out


def tool_fincheck_ingest(from_dir: str, run_id: str = "mcp_ingest",
                         offline: bool = True,
                         report_period: str = "annual") -> dict:
    """给一个文件夹，按**文件内容**识别材料并跑完整流程（年报 / 研报）。"""
    from src.ingest import ingest
    from src.report import build_all

    ingest(from_dir, run_id, offline=offline,
           default_year=None, report_period=report_period)
    made = build_all(run_id, ("pdf", "xlsx", "html"))
    return {**_run_summary(run_id), "reports": made}


def tool_fincheck_run(config: str = "configs/demo.yaml", run_id: str = "mcp_run",
                      offline: bool = True) -> dict:
    """跑已有结构化数据（configs/demo.yaml）的完整核查流程。"""
    from src.pipeline import run
    from src.report import build_all

    run(config, offline=offline, run_id=run_id)
    made = build_all(run_id, ("pdf", "xlsx", "html"))
    return {**_run_summary(run_id), "reports": made}


def tool_fincheck_report(run_id: str, formats: str = "pdf,xlsx,html") -> dict:
    """只重生成核查报告（PDF / Excel / 网页）。"""
    from src.report import build_all

    made = build_all(run_id, tuple(f.strip() for f in formats.split(",") if f.strip()))
    return {"run_id": run_id, "reports": made}


def tool_calculate_ratios(records: list) -> dict:
    """确定性财务比率计算（盈利能力 / 费用率 / 趋势 / 结构四组，不落盘、不调模型）。"""
    from src.financial_analysis import analyze
    return analyze(records)


def tool_check_consistency(records: list, run_id: str = "mcp_consistency",
                           offline: bool = True) -> dict:
    """一致性规则核查 + CSI 评分（纯确定性，不调模型、不联网）。"""
    from src.rules_engine import run_rules
    return run_rules(records, run_id, offline=offline)


# 工具注册表：名称 → (描述, JSON Schema 入参, 实现)
TOOLS: dict[str, dict] = {
    "fincheck_ingest": {
        "description": "给一个文件夹，按文件内容自动识别年报/研报并跑完整核查流程，"
                       "产物与三端报告落在 runs/<run_id>/。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_dir": {"type": "string", "description": "材料文件夹路径"},
                "run_id": {"type": "string", "description": "运行编号"},
                "offline": {"type": "boolean", "description": "离线模式（只读缓存）", "default": True},
                "report_period": {"type": "string", "enum": ["annual", "half_year"],
                                  "default": "annual"},
            },
            "required": ["from_dir"],
        },
        "fn": tool_fincheck_ingest,
    },
    "fincheck_run": {
        "description": "对已有结构化数据执行完整核查流程（默认 configs/demo.yaml），"
                       "并生成三端报告。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "config": {"type": "string", "default": "configs/demo.yaml"},
                "run_id": {"type": "string", "default": "mcp_run"},
                "offline": {"type": "boolean", "default": True},
            },
        },
        "fn": tool_fincheck_run,
    },
    "fincheck_report": {
        "description": "为一已有运行重新生成核查报告（PDF / Excel / 网页）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "formats": {"type": "string", "default": "pdf,xlsx,html"},
            },
            "required": ["run_id"],
        },
        "fn": tool_fincheck_report,
    },
    "calculate_ratios": {
        "description": "确定性财务比率计算：输入结构化记录（item_code/period/value），"
                       "返回盈利能力/费用率/趋势/结构四组指标，不调模型。",
        "inputSchema": {
            "type": "object",
            "properties": {"records": {"type": "array", "items": {"type": "object"}}},
            "required": ["records"],
        },
        "fn": tool_calculate_ratios,
    },
    "check_consistency": {
        "description": "一致性规则核查 + CSI 评分：输入结构化记录，返回规则结果与维度得分，"
                       "纯确定性、不调模型、不联网。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "records": {"type": "array", "items": {"type": "object"}},
                "run_id": {"type": "string", "default": "mcp_consistency"},
                "offline": {"type": "boolean", "default": True},
            },
            "required": ["records"],
        },
        "fn": tool_check_consistency,
    },
}


# ---------------------------------------------------------------- 资源

def _list_runs() -> list[str]:
    d = _runs_dir()
    if not d.exists():
        return []
    return sorted((p.name for p in d.iterdir() if p.is_dir()), reverse=True)


def resource_list() -> list[dict]:
    return [
        {"uri": "fincheck://runs", "name": "运行编号列表",
         "description": "runs/ 下的历史运行编号（倒序）", "mimeType": "application/json"},
        {"uri": "fincheck://runs/{run_id}/manifest.json", "name": "运行清单",
         "description": "模型标识、提示词哈希、输入哈希、调用统计",
         "mimeType": "application/json"},
        {"uri": "fincheck://runs/{run_id}/rules/csi.json", "name": "一致性指数",
         "description": "CSI 总分与各维度得分明细", "mimeType": "application/json"},
    ]


def resource_read(uri: str) -> dict:
    """按 URI 读取资源；路径越界或不存在时抛 ValueError（由上层转协议错误）。"""
    if uri == "fincheck://runs":
        return {"uri": uri, "mimeType": "application/json",
                "text": json.dumps(_list_runs(), ensure_ascii=False)}
    prefix = "fincheck://runs/"
    if not uri.startswith(prefix):
        raise ValueError(f"未知资源 URI：{uri}")
    rel = uri[len(prefix):]
    target = (_runs_dir() / rel).resolve()
    if _runs_dir().resolve() not in target.parents and target != _runs_dir().resolve():
        raise ValueError(f"资源路径越界：{uri}")
    if not target.is_file():
        raise ValueError(f"资源不存在：{uri}")
    return {"uri": uri, "mimeType": "application/json",
            "text": target.read_text(encoding="utf-8")}


# ---------------------------------------------------------------- 协议分发

def _tool_result(payload) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False,
                                                            default=str)}],
            "isError": False}


def _tool_error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def handle(request: dict) -> dict | None:
    """处理一条 JSON-RPC 消息，返回响应；通知类（无 id）返回 None。"""
    method = request.get("method")
    rid = request.get("id")
    params = request.get("params") or {}

    if rid is None:                       # 通知，无需响应
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    if method == "initialize":
        return ok({"protocolVersion": PROTOCOL_VERSION,
                   "capabilities": {"tools": {}, "resources": {}},
                   "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": [{"name": name, "description": spec["description"],
                              "inputSchema": spec["inputSchema"]}
                             for name, spec in TOOLS.items()]})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        spec = TOOLS.get(name)
        if not spec:
            return ok(_tool_error(f"未知工具：{name}"))
        # 工具执行期间的库输出重定向到 stderr，保护协议流不被污染
        with contextlib.redirect_stdout(sys.stderr):
            try:
                return ok(_tool_result(spec["fn"](**args)))
            except TypeError as e:        # 入参不符
                return ok(_tool_error(f"入参不合法：{e}"))
            except Exception as e:        # noqa: BLE001 - 工具失败如实回报
                return ok(_tool_error(f"{type(e).__name__}: {e}"))
    if method == "resources/list":
        return ok({"resources": resource_list()})
    if method == "resources/read":
        try:
            return ok({"contents": [resource_read(params.get("uri", ""))]})
        except ValueError as e:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": str(e)}}
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"不支持的方法：{method}"}}


# ---------------------------------------------------------------- 传输

def serve(stdin=None, stdout=None) -> None:
    """stdio 传输：逐行读取 JSON-RPC 消息，逐行写回响应。"""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": "JSON 解析失败"}}
        else:
            try:
                resp = handle(request)
            except Exception:            # noqa: BLE001 - 顶层兜底，绝不让进程崩
                resp = {"jsonrpc": "2.0", "id": request.get("id"),
                        "error": {"code": -32603,
                                  "message": "内部错误：" + traceback.format_exc(limit=1)}}
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()


# ---------------------------------------------------------------- 自检

def selftest() -> int:
    """离线自检：验证 initialize / tools/list / resources/read / calculate_ratios 四个往返。"""
    buf = io.StringIO()
    current_dir = str(Path(__file__).resolve().parents[1])

    def emit(req):
        buf.write(json.dumps(req, ensure_ascii=False) + "\n")

    emit({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": PROTOCOL_VERSION,
                     "capabilities": {}, "clientInfo": {"name": "selftest", "version": "0"}}})
    emit({"jsonrpc": "2.0", "method": "notifications/initialized"})   # 通知，不应有响应
    emit({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    emit({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
          "params": {"name": "calculate_ratios", "arguments": {"records": [
              {"item_code": "TOTAL_REVENUE", "period": "2024", "value": "1741.4"},
              {"item_code": "NET_PROFIT", "period": "2024", "value": "862.3"}]}}})
    emit({"jsonrpc": "2.0", "id": 4, "method": "resources/read",
          "params": {"uri": "fincheck://runs"}})

    buf.seek(0)
    out = io.StringIO()
    serve(stdin=buf, stdout=out)

    lines = [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]
    assert len(lines) == 4, f"应收到 4 条响应（通知不响应），实际 {len(lines)}"
    by_id = {r["id"]: r for r in lines}
    assert by_id[1]["result"]["serverInfo"]["name"] == SERVER_NAME
    names = [t["name"] for t in by_id[2]["result"]["tools"]]
    assert set(names) == set(TOOLS), f"工具清单不符：{names}"
    assert by_id[3]["result"]["isError"] is False
    assert "metrics" in json.loads(by_id[3]["result"]["content"][0]["text"])
    assert "contents" in by_id[4]["result"]

    print(f"MCP 服务自检通过：协议 {PROTOCOL_VERSION}，工具 {len(TOOLS)} 个，"
          f"资源 {len(resource_list())} 个（cwd={current_dir}）")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return selftest()
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
