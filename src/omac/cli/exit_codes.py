"""退出码契约(设计文档 §5.1)— 稳定,调用方可脚本分支。"""

OK = 0               # 成功 / DAG 收敛全部 done
GENERIC = 1          # 通用错误
PLATFORM = 2         # 平台/网络错误
AUTH = 3             # 认证错误(平台 CLI 未登录等)
VALIDATION = 5       # 校验失败(lint / 证据 schema)
IN_PROGRESS = 10     # 推进中(仅单轮 tick 模式)
NEEDS_DECISION = 20  # 需要调用者决策(附结构化报告)
