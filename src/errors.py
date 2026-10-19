# src/errors.py
"""错误分类与提示。本文件不依赖任何其他项目模块，可被 llm.py、cli.py、测试脚本共用。"""

class ProviderError(RuntimeError):
    """不可重试的调用错误（配置/凭据/额度问题）"""
    def __init__(self, code, msg):
        self.code, self.msg = code, msg
        super().__init__(f"[{code}] {msg}")


# ★ 这两组集合在模块最外层（不要缩进到类里面）
RETRYABLE = {429, 500, 503}          # 外面的事，等会儿再来
NEVER     = {400, 401, 402, 422}     # 我们的问题，改了再来

HINTS = {
    400: "请求体格式错误（最常见：模型名写错、参数拼写错误）",
    401: "认证失败（Key 无效、带了空格，或已被吊销）",
    402: "余额不足（请充值，或改用 --offline 使用缓存）",
    422: "参数错误（请检查 model / max_tokens 等写法）",
}


def classify(e) -> tuple[bool, str, int | None]:
    """返回 (是否可重试, 人话提示, 错误码)"""
    code = getattr(e, "status_code", None)
    if code in RETRYABLE:
        return True, f"服务端/限流问题（{code}），将退避重试", code
    if code in NEVER:
        return False, HINTS[code], code
    if code is None:                 # 超时、连接失败等
        return True, "网络异常，将退避重试", None
    return False, f"未知错误 {code}", code