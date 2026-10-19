# mcp_server/__main__.py
"""``python -m mcp_server`` 入口：启动 stdio MCP 服务或跑离线自检。"""
from mcp_server.server import main

if __name__ == "__main__":
    raise SystemExit(main())
