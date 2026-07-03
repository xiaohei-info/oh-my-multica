"""pipeline — 编排流程层(骨架占位,按路线图填充)。

- loop.py      P1:dag run/tick/status —— sync(回收结果)→ decide(就绪节点)
               → dispatch(派发·经 Store+Runtime),退出码契约,无自动重试
- dispatch.py  P2:派发 issue body 三段式模板、work show/submit 的证据左移校验
- plan.py      P3:计划 → 验收文档 → 拆解流水线(同一 issue 转派的 review 阶段)
- delivery.py  P4:CI 监控回退、自动 merge、总控验收 + DAG 增量扩展外层循环

纪律:本层只调 engines 的 Store/Runtime 接口,绝不直接 shell out 平台 CLI。
"""
