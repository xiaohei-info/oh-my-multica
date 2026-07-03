"""omac 错误类型 — 与退出码契约一一对应(设计文档 §5.1)。

cli.main 统一捕获这些异常并转换为对应退出码,
业务层只管 raise,不直接 sys.exit。
"""


class OmacError(Exception):
    """通用错误 → exit 1。"""
    exit_code = 1


class PlatformError(OmacError):
    """平台/网络错误(引擎调用失败等)→ exit 2。"""
    exit_code = 2


class AuthError(OmacError):
    """认证错误(平台 CLI 未登录等)→ exit 3。"""
    exit_code = 3


class ValidationError(OmacError):
    """校验失败(lint / 证据 schema / 参数)→ exit 5。"""
    exit_code = 5


class NeedsDecision(OmacError):
    """需要调用者决策 → exit 20。携带结构化报告(report dict)。"""
    exit_code = 20

    def __init__(self, message: str, report: dict | None = None):
        super().__init__(message)
        self.report = report or {}
