"""omac 错误类型 — 与退出码契约一一对应(docs/design/README.md §5.1)。

cli.main 统一捕获这些异常并转换为对应退出码,
业务层只管 raise,不直接 sys.exit。
"""


class OmacError(Exception):
    """通用错误 → exit 1。"""
    exit_code = 1


class PlatformError(OmacError):
    """平台/网络错误(引擎调用失败等)→ exit 2。"""
    exit_code = 2


class ObservationConflict(PlatformError):
    """并发写使乐观读失效(读取期间工作项被改动)。

    不是平台故障:Loop 在逐项处理处捕获,该项本轮跳过、下轮重新观察。
    未被捕获(CLI 单项命令)时仍按平台错误 exit 2。
    """


class NodeFailure(PlatformError):
    """单个节点的交付或控制事实不自洽(平台本身有应答,不是平台故障)。

    Loop 在逐节点处理处捕获:只停这个节点(写带 ``reason_code`` 的 decision),或把作废的交付退回 Worker,
    其余节点照常推进。``gate`` 指明用哪个阶段的恢复决策(``worker`` / ``reviewer``)。
    未被捕获(CLI 单项命令)时仍按平台错误 exit 2。
    """

    def __init__(self, message: str, *, reason_code: str, gate: str = "worker"):
        super().__init__(message)
        self.reason_code = reason_code
        self.gate = gate


class MetadataBudgetError(PlatformError, ValueError):
    """工作项 metadata 超出平台聚合上限,且无可还原的压缩空间(失败关闭)。

    也是 ValueError:兼容此前直接抛 ValueError 的预算检查调用方。
    """


class WorkItemNotFoundError(PlatformError, RuntimeError):
    """适配器已明确证明目标工作单元不存在。"""


class AuthError(OmacError):
    """认证错误(平台 CLI 未登录等)→ exit 3。"""
    exit_code = 3


class ValidationError(OmacError):
    """校验失败(lint / 证据 schema / 参数)→ exit 5。"""
    exit_code = 5


class ManifestLockedError(ValidationError):
    """manifest 写锁被另一个进程持有(exit 5,与此前一致)。写命令据此改走本机写命令队列。"""


class NeedsDecision(OmacError):
    """需要调用者决策 → exit 20。携带结构化报告(report dict)。"""
    exit_code = 20

    def __init__(self, message: str, report: dict | None = None):
        super().__init__(message)
        self.report = report or {}
