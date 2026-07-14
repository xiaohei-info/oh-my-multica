"""用户可见文案的语言解析与翻译目录。"""
from __future__ import annotations

from typing import Mapping

from .errors import ValidationError

EN = "en"
CN = "cn"
SUPPORTED_LANGUAGES = (EN, CN)

_MESSAGES = {
    EN: {
        "config.language.prompt": "Language (en/cn)",
        "work.protocol.plan": (
            "Write the design document: analyze the request and produce an "
            "executable, verifiable plan anchored in acceptance goals."
        ),
        "work.protocol.acceptance": (
            "Write the acceptance document: turn each business flow in the "
            "approved design into an executable, end-to-end user journey."
        ),
        "work.protocol.decompose": (
            "Turn the design and acceptance document into a manifest DAG: "
            "every node has a complete contract, acceptance points to the "
            "acceptance document, and the DAG is acyclic."
        ),
        "work.protocol.develop": (
            "Push a branch and open a PR (base=contract.pr_base; the worker "
            "creates it, OMAC does not). Work test-first and submit structured "
            "verification evidence; do not manually change the issue status, "
            "assignee, rerun, or cancel state."
        ),
        "work.protocol.final_acceptance": (
            "Use the acceptance document as a checklist for an end-to-end "
            "user-journey review. Record pass/fail and evidence for every item."
        ),
        "work.protocol.review": (
            "Reproduce independently: prepare the environment from env_setup, "
            "rerun verification and integration tests, keep shared state "
            "read-only, and issue a verdict from the contract and acceptance goals."
        ),
        "work.protocol.pr_link": (
            "Include `{issue_key}` in the GitHub PR branch name, title, or body "
            "so Multica can link the PR to this issue automatically. Delivery "
            "still works when the key is absent."
        ),
        "work.authority.current": "Current facts from work show",
        "work.authority.contract": "contract / previous_review",
        "work.authority.role": "role guide",
        "work.authority.artifact": "artifact guide",
        "work.authority.workflow": "workflow overview",
        "guide.index.title": "Agent Guide index (stable static knowledge):",
        "guide.index.first": "  First run: omac work show <id> --output json",
        "guide.index.rule": (
            "  Then load only guide_refs; instance facts take priority and the "
            "Guide never overrides current task facts."
        ),
        "guide.index.topics": "Available topics:",
        "guide.index.roles": "Role protocols:",
        "guide.index.artifacts": "Artifact formats:",
        "guide.unknown": (
            "Unknown guide topic: {requested}\nAvailable topics: {valid}\n"
            "Example: omac guide role worker / omac guide artifact manifest\n"
            "Run `omac guide` first; do not guess a topic."
        ),
        "work.table.task": "Task",
        "work.table.title": "title",
        "work.table.status": "status",
        "work.table.identity": "identity",
        "work.table.bounces": "bounce count",
        "work.table.author": "author",
        "work.table.issue_context": "Issue context",
        "work.table.review_target": "Review target",
        "work.table.rerun_setup": "Reproduction setup (env_setup)",
        "work.table.review_basis": "Review basis (contract)",
        "work.table.contract": "Your complete contract",
        "work.table.issue_detail": "Read task detail and requirements in the issue body (briefing / upstream artifacts).",
        "work.table.previous_review": "Previous review",
        "work.table.now": "What to do now",
        "work.table.authority": "Authority order",
        "work.table.guides": "Guides to read",
        "work.table.submit": "Submit when finished",
        "work.source.title": "Upstream issues (stay on target)",
        "work.source.body": (
            "This task follows these upstream issues for requirements and "
            "decisions. If they conflict with the current contract, ask for "
            "confirmation first:"
        ),
        "init.engine.options": "Available engines: {engines}",
        "init.engine.prompt": "Choose engine",
        "init.help.check": "Check configuration without writing files",
        "init.help.engine": "Engine type: multica or mock",
        "init.help.workspace": "Workspace ID",
        "init.help.project": "Project ID (required by multica; interactive setup can select or create one)",
        "init.help.planner": "Planner agent name",
        "init.help.orchestrator": "Orchestrator agent name",
        "init.help.workers": "Worker agent names, separated by commas",
        "init.help.reviewers": "Reviewer agent names, separated by commas",
        "init.help.acceptor": "Optional acceptor agent; defaults to the reviewer pool",
        "init.help.max_parallel": "Default maximum parallel DAG tasks (defaults.max_parallel)",
        "init.help.retry_worker": "Maximum worker retries after a run ends without submit (retry.worker; 0 blocks immediately)",
        "init.help.retry_ci": "Maximum worker retries after CI failure (retry.ci; 0 blocks immediately)",
        "init.help.retry_review": "Maximum worker retries after reviewer rejection (retry.review; 0 blocks immediately)",
        "init.help.retry_merge": "Maximum worker retries after merge failure (retry.merge; 0 blocks immediately)",
        "output.help": "Output format: json for agents and Web; table for human inspection (default: {default})",
        "config.help.key": "Dotted key, for example roles.planner; omit to return the full configuration",
        "config.help.set_key": "Dotted configuration key",
        "config.help.value": "Value parsed as YAML",
        "guide.help.topic": "Topic to read: workflow|roles|recovery, or a role/artifact group",
        "cli.root.description": "oh-my-multica — Deterministic CLI orchestration for parallel multi-agent delivery",
        "cli.group.core": "CORE COMMANDS (caller / operator)",
        "cli.group.work": "WORK COMMANDS (dispatched agent)",
        "cli.group.setup": "SETUP COMMANDS",
        "cli.group.guide": "GUIDE COMMANDS",
        "cli.group.web": "WEB COMMANDS",
        "cli.command.plan": "Design and manifest-DAG pipeline with review gates",
        "cli.command.dag": "Inspect and run a manifest DAG",
        "cli.command.node": "Make explicit exit-20 node decisions",
        "cli.command.work": "Agent interface for task facts and structured delivery",
        "cli.command.init": "Interactive setup and --check health check",
        "cli.command.config": "Read and write project configuration",
        "cli.command.guide": "Load stable workflow knowledge named by guide_refs",
        "cli.command.web": "Local read-only dashboard for progress and evidence",
        "cli.description.plan": "Create, inspect, confirm, or resume the design-to-manifest pipeline.",
        "cli.description.dag": "Validate, inspect, and run a manifest DAG deterministically.",
        "cli.description.node": "Inspect exit-20 evidence and make explicit retry, accept, or abandon decisions.",
        "cli.description.work": "Agent interface: show and submit default to JSON. Read current task facts, then submit one structured deliverable.",
        "cli.description.init": "Configure an OMAC project interactively, or validate an existing configuration.",
        "cli.description.config": "Read and write .omac/config.yaml with dotted keys.",
        "cli.description.guide": "Agent guide: run `omac work show <id> --output json` first, then load stable static knowledge for the current task facts.",
        "cli.description.web": "Start the local read-only dashboard. API responses are command JSON unchanged.",
    },
    CN: {
        "config.language.prompt": "语言（en/cn）",
        "work.protocol.plan": "编写设计方案：分析需求，产出锚定验收目标、可执行、可验证的方案文档。",
        "work.protocol.acceptance": "编写验收文档：把定稿设计方案的业务流程逐条转成用户视角、端到端、可执行的验收动作。",
        "work.protocol.decompose": "把设计方案和验收文档拆成 manifest DAG：每个节点都有完整 contract，acceptance 锚定验收文档，DAG 无环。",
        "work.protocol.develop": "推分支并开 PR（base=contract.pr_base，由 worker 创建，OMAC 不代建）。按 TDD 工作并提交结构化验证证据；不要手动修改 issue 状态、assignee、rerun 或 cancel 状态。",
        "work.protocol.final_acceptance": "以验收文档为清单做用户视角端到端走查，逐条记录 pass/fail 和证据。",
        "work.protocol.review": "独立复跑：按 env_setup 搭建环境，重跑验证命令与集成测试，只读共享状态，并依据 contract 与验收目标给出 verdict。",
        "work.protocol.pr_link": "建议让 GitHub PR 的分支名、标题或正文包含 `{issue_key}`，这样 Multica 可以自动关联该 issue；缺失时仍可交付。",
        "work.authority.current": "work show 当前实例事实",
        "work.authority.contract": "contract / previous_review",
        "work.authority.role": "role guide",
        "work.authority.artifact": "artifact guide",
        "work.authority.workflow": "workflow 总览",
        "guide.index.title": "Agent Guide 索引（稳定静态知识）：",
        "guide.index.first": "  先运行：omac work show <id> --output json",
        "guide.index.rule": "  再按 guide_refs 最小加载；实例事实优先，Guide 不覆盖当前任务事实。",
        "guide.index.topics": "可用 topic：",
        "guide.index.roles": "角色协议：",
        "guide.index.artifacts": "产物格式：",
        "guide.unknown": (
            "未知 guide topic：{requested}\n可用 topic：{valid}\n"
            "示例：omac guide role worker / omac guide artifact manifest\n"
            "先运行 `omac guide` 查看列表，不要猜 topic。"
        ),
        "work.table.task": "任务",
        "work.table.title": "标题",
        "work.table.status": "状态",
        "work.table.identity": "身份",
        "work.table.bounces": "回退计数",
        "work.table.author": "产出者",
        "work.table.issue_context": "Issue 上下文",
        "work.table.review_target": "评审对象",
        "work.table.rerun_setup": "复跑清单（env_setup）",
        "work.table.review_basis": "评审依据（contract）",
        "work.table.contract": "你的 contract（完整）",
        "work.table.issue_detail": "任务详情与需求见本 issue 正文（briefing / 上游产物段）。",
        "work.table.previous_review": "上轮评审",
        "work.table.now": "现在做什么",
        "work.table.authority": "权威顺序",
        "work.table.guides": "需要读取的 Guide",
        "work.table.submit": "完成后交付",
        "work.source.title": "上游 issue（防跑偏）",
        "work.source.body": "本任务承接以下上游 issue，用于追溯需求与决策；若与当前 contract 冲突，先请求确认：",
        "init.engine.options": "可选引擎：{engines}",
        "init.engine.prompt": "选择引擎",
        "init.help.check": "体检模式，不写任何文件",
        "init.help.engine": "引擎类型（multica 或 mock）",
        "init.help.workspace": "工作空间 ID",
        "init.help.project": "项目 ID（multica 必填；交互模式可选择或新建）",
        "init.help.planner": "planner Agent 名称",
        "init.help.orchestrator": "orchestrator Agent 名称",
        "init.help.workers": "worker Agent 名称，逗号分隔",
        "init.help.reviewers": "reviewer Agent 名称，逗号分隔",
        "init.help.acceptor": "可选 acceptor Agent，默认复用 reviewers 池",
        "init.help.max_parallel": "DAG 默认最大并行任务数（defaults.max_parallel）",
        "init.help.retry_worker": "worker 运行结束但未 submit 的最大回退次数（retry.worker；0 立即 blocked）",
        "init.help.retry_ci": "CI 失败后回到 worker 的最大次数（retry.ci；0 立即 blocked）",
        "init.help.retry_review": "reviewer reject 后回到 worker 的最大次数（retry.review；0 立即 blocked）",
        "init.help.retry_merge": "merge 失败后回到 worker 的最大次数（retry.merge；0 立即 blocked）",
        "output.help": "输出格式：json 供 Agent/Web 使用；table 供人类查看（默认：{default}）",
        "config.help.key": "点分键，例如 roles.planner；省略时输出整份配置",
        "config.help.set_key": "点分配置键",
        "config.help.value": "按 YAML 解析的值",
        "guide.help.topic": "要阅读的 topic：workflow|roles|recovery，或 role/artifact 分组",
        "cli.root.description": "oh-my-multica — 确定性 CLI 驱动的多 Agent 并行开发编排",
        "cli.group.core": "CORE COMMANDS（调用者 / 驱动侧）",
        "cli.group.work": "WORK COMMANDS（被派发 Agent 侧）",
        "cli.group.setup": "SETUP COMMANDS",
        "cli.group.guide": "GUIDE COMMANDS",
        "cli.group.web": "WEB COMMANDS",
        "cli.command.plan": "设计方案与 manifest DAG 拆解流水线（内置 review 门）",
        "cli.command.dag": "检查、观察并执行 manifest DAG",
        "cli.command.node": "对 exit 20 节点作显式决策",
        "cli.command.work": "Agent 读取实例事实并提交结构化交付的接口",
        "cli.command.init": "交互式配置与 --check 体检",
        "cli.command.config": "读写项目配置",
        "cli.command.guide": "按 guide_refs 加载稳定工作流知识",
        "cli.command.web": "本地只读进度与证据面板",
        "cli.description.plan": "创建、查看、确认或恢复从设计到 manifest 的流水线。",
        "cli.description.dag": "确定性地校验、查看并执行 manifest DAG。",
        "cli.description.node": "查看 exit 20 证据，并显式决定 retry、accept 或 abandon。",
        "cli.description.work": "Agent 接口：读取当前任务事实，再提交一种结构化交付物。",
        "cli.description.init": "交互式配置 OMAC 项目，或校验已有配置。",
        "cli.description.config": "通过点分键读写 .omac/config.yaml。",
        "cli.description.guide": "读取 work show 当前实例事实后，加载稳定静态知识。",
        "cli.description.web": "启动本地只读面板；API 原样返回命令 JSON。",
    },
}


def resolve_language(config: Mapping | None) -> str:
    """Return the configured language, defaulting missing values to English."""
    value = (config or {}).get("language")
    if value is None:
        return EN
    if value in SUPPORTED_LANGUAGES:
        return value
    raise ValidationError(
        f"language must be one of: {', '.join(SUPPORTED_LANGUAGES)}; got {value!r}")


def current_language() -> str:
    """Read the project language without creating an import cycle at module load."""
    from .core import config as config_mod
    return resolve_language(config_mod.load_config())


def ui(english: str, chinese: str, *, language: str | None = None) -> str:
    """Select complete user-visible copy; English is always the default."""
    selected = current_language() if language is None else resolve_language(
        {"language": language})
    return english if selected == EN else chinese


def t(key: str, *, language: str = EN, **values) -> str:
    """Render one OMAC-owned message in a validated language."""
    language = resolve_language({"language": language})
    try:
        template = _MESSAGES[language][key]
    except KeyError as exc:
        raise KeyError(f"unknown localization message: {key}") from exc
    return template.format(**values)
