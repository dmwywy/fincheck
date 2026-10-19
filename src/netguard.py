# src/netguard.py
"""联网边界：只允许访问白名单地址，其余一律拦截并记录。"""
import socket, logging
from src.logger import log_event

ALLOW_HOST = "api.deepseek.com"
ALLOW_PORT = 443
# 本机回环一律放行：它不是"对外连接"，拦它没有安全收益，
# 却会让网页界面（Streamlit 在 localhost 上服务）和任何本地 IPC 直接崩掉。
LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}
_orig_connect = socket.socket.connect


def is_allowed(host: str, port: int) -> bool:
    if host in LOOPBACK:
        return True
    return (host == ALLOW_HOST or host.endswith("." + ALLOW_HOST)) and port == ALLOW_PORT


def install_allowlist(run_id: str = "default") -> None:
    """只允许连接白名单地址（含本机回环），其他一律拦截并写日志"""
    def guarded(self, address):
        host, port = address[0], address[1]
        ok = is_allowed(host, port)
        try:
            log_event(run_id, "network", host=host, port=port, allowed=ok,
                      purpose="llm_api" if ok else ("loopback" if host in LOOPBACK else "blocked"))
        except Exception:
            pass                      # 日志失败不能影响主流程
        if not ok:
            logging.error("[NETGUARD] 拦截非白名单连接 %s:%s", host, port)
            raise PermissionError(f"禁止访问白名单外的地址：{host}:{port}")
        return _orig_connect(self, address)
    socket.socket.connect = guarded


def uninstall_allowlist() -> None:
    """还原 socket.connect。

    本函数是给测试用的：install 会**全局**改写 socket.socket.connect，
    不还原就会污染同一进程里后续的所有测试（实测把 Streamlit 测试全弄挂了）。
    """
    socket.socket.connect = _orig_connect


def offline_selftest() -> bool:
    """离线模式启动自检：能连上 API 就说明有模块偷偷联网了"""
    s = socket.socket(); s.settimeout(0.5)
    try:
        s.connect((ALLOW_HOST, ALLOW_PORT))
        raise RuntimeError("离线模式下检测到网络可达，请检查是否有模块绕过缓存直接调用 API")
    except OSError:
        return True
    finally:
        s.close()