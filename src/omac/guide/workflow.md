# omac 整体工作流

omac 是确定性 CLI 驱动的多 Agent 并行开发编排:Loop 驱动 Agent,LLM 只做
被派发的、有终点的专家任务(planner / orchestrator / reviewer / worker / acceptor)。

## 标准路径

1. `omac init` —— 一次性配置:选 workspace → 列全量 agent → 角色映射
   → 落盘 .orchestrator/config.yaml(体检:`omac init --check`)
2. `omac plan create --name <feature> [--doc 设计文档]` —— 计划 → 验收文档 → 拆解,
   全程内置 review 阶段,产出 .orchestrator/<feature>.yaml(+ .acceptance.yaml)
3. `omac dag run .orchestrator/<feature>.yaml` —— 确定性 loop:
   回收结果 → 计算就绪节点 → 派发,直到收敛;收敛后进入总控验收外层循环
   - exit 0:验收全部 pass,真正可交付
   - exit 20:无法继续推进,stdout 有结构化报告(含可执行的下一步命令)
4. exit 20 后:`omac dag status` 看全景 → `omac node show <key>` 看证据链
   → `omac node retry|abandon` 决策 → 重跑 `omac dag run`(重跑即续跑)

## 关键原则

- issue 的范围 = 一个完整阶段:产出、评审、回退都在同一条 issue 上,交接 = 转派
- 重试是显式决策,不自动发生
- 全部状态在 manifest + 平台,任意中断可恢复,支持跨机器接力
- 前置:runtime 机器需安装 omac(pipx install omac)与已登录的平台 CLI

完整设计:docs/omac-cli-design.md
