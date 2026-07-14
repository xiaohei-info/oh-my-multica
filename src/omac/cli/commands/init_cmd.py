"""omac init — 交互式配置 / --check 体检(设计文档 §7.1)。

两种模式:
  omac init            交互式:引擎 → workspace → 可选模板 agent → 角色映射 → 落盘 config.yaml
  omac init --check    体检:本地 + 引擎可达时校验 workspace 存在、各角色 agent 在池内

兼容路径(全参数直出):
  omac init --engine mock --workspace ws --planner a --orchestrator b \\
            --workers c,d --reviewers e,f [--acceptor g] [--max-parallel 4]
            [--retry-ci 3 --retry-review 3 --retry-merge 3]

红线:init 只调引擎接口(WorkItemStore / AgentRuntime),
绝不直接 shell out 平台 CLI(§12.4)。
"""
from __future__ import annotations

import shutil
import sys
from typing import List, Optional

from ...agent_templates import AgentTemplateCatalog
from ...core import config as config_mod
from ...engines import ENGINE_TYPES, create_engine
from ...engines.models import AgentProvisionSpec, EngineConfig, RuntimeTarget, WorkspaceInfo
from ...errors import OmacError, ValidationError
from ...i18n import CN, EN, resolve_language, t, ui
from .. import exit_codes

NAME = "init"
SUMMARY = "交互式配置 / --check 体检"
DESCRIPTION = """一次性配置:选定 workspace → 可选地从内置模板创建 agent → 完成角色映射,
固化进 .omac/config.yaml(不引入小队/分组等平台特有概念)。

  omac init            交互式生成配置;可选择使用已有 agent,或从 planner /
                       orchestrator / worker / reviewer / acceptor / architect /
                       backend / frontend / pm 模板创建
  omac init --check    体检:multica CLI 是否在 PATH / 配置文件是否存在且含
                       engine·workspace·roles / 各角色 agent 是否在工作空间内

agent/脚本入口:
  用 omac config set 写入配置后,只运行 omac init --check 做体检。
  裸 omac init 是人类交互式向导,非 TTY 下会直接报错并给出 config set 示例。

兼容路径(全参数零交互直出):
  omac init --engine mock --workspace ws \\
            --planner alice --orchestrator bob \\
            --workers carol,dave --reviewers eve,frank [--acceptor grace] [--max-parallel 4]
            [--retry-ci 3 --retry-review 3 --retry-merge 3]
"""

_INIT_LANGUAGE = EN


def _copy(english: str, chinese: str) -> str:
    return ui(english, chinese, language=_INIT_LANGUAGE)


def register(parser):
    language = resolve_language(config_mod.load_config())
    parser.add_argument("--check", action="store_true", help=t("init.help.check", language=language))
    parser.add_argument("--engine", choices=list(ENGINE_TYPES), help=t("init.help.engine", language=language))
    parser.add_argument("--workspace", help=t("init.help.workspace", language=language))
    parser.add_argument("--project", help=t("init.help.project", language=language))
    parser.add_argument("--planner", help=t("init.help.planner", language=language))
    parser.add_argument("--orchestrator", help=t("init.help.orchestrator", language=language))
    parser.add_argument("--workers", help=t("init.help.workers", language=language))
    parser.add_argument("--reviewers", help=t("init.help.reviewers", language=language))
    parser.add_argument("--acceptor", help=t("init.help.acceptor", language=language))
    parser.add_argument("--max-parallel", type=int,
                        help=t("init.help.max_parallel", language=language))
    parser.add_argument("--retry-worker", type=int,
                        help=t("init.help.retry_worker", language=language))
    parser.add_argument("--retry-ci", type=int,
                        help=t("init.help.retry_ci", language=language))
    parser.add_argument("--retry-review", type=int,
                        help=t("init.help.retry_review", language=language))
    parser.add_argument("--retry-merge", type=int,
                        help=t("init.help.retry_merge", language=language))


# ==================== 工具 ====================

def _split_csv(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]


def _as_list(val) -> List[str]:
    if val is None:
        return []
    if isinstance(val, str):
        return _split_csv(val)
    if isinstance(val, (list, tuple)):
        return [str(x).strip() for x in val if str(x).strip()]
    return [str(val)]


def _build_store(engine_type: str, workspace_id: str = ""):
    """按引擎类型构造一个 Store(init 只用 list_workspaces / list_members)。"""
    config = EngineConfig(engine_type=engine_type, workspace_id=workspace_id)
    return create_engine(engine_type, config).store


def _build_engine(engine_type: str, workspace_id: str = ""):
    config = EngineConfig(engine_type=engine_type, workspace_id=workspace_id)
    return create_engine(engine_type, config)


def _prompt(message: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default else ""
    raw = input(f"{message}{suffix}: ").strip()
    return raw or (default or "")


def _prompt_bool(message: str, default: bool) -> bool:
    raw = _prompt(message, "Y" if default else "n").lower()
    if raw in ("y", "yes", "true", "1", "是"):
        return True
    if raw in ("n", "no", "false", "0", "否"):
        return False
    raise ValidationError(_copy(
        f"{message} accepts yes/no only",
        f"{message} 只接受 yes/no"))


def _validate_max_parallel(value: int) -> int:
    if isinstance(value, bool) or value < 1:
        raise ValidationError(_copy(
            f"defaults.max_parallel must be a positive integer; got {value!r}",
            f"defaults.max_parallel 必须为正整数,got {value!r}"))
    return value


def _select_max_parallel(args_val: Optional[int], interactive: bool) -> int:
    if args_val is not None:
        return _validate_max_parallel(args_val)
    default = config_mod.DEFAULTS["max_parallel"]
    if not interactive:
        return default
    raw = _prompt(_copy(
        "Default maximum parallel tasks (defaults.max_parallel)",
        "默认最大并行任务数(defaults.max_parallel)"), str(default))
    try:
        return _validate_max_parallel(int(raw))
    except ValueError:
        raise ValidationError(_copy(
            f"defaults.max_parallel must be a positive integer; got {raw!r}",
            f"defaults.max_parallel 必须为正整数,got {raw!r}"))


def _validate_retry_value(key: str, value: int) -> int:
    if isinstance(value, bool) or value < 0:
        raise ValidationError(_copy(
            f"retry.{key} must be a non-negative integer; got {value!r}",
            f"retry.{key} 必须为非负整数,got {value!r}"))
    return value


def _select_retry(args, interactive: bool) -> dict:
    retry = dict(config_mod.DEFAULT_RETRY)
    arg_map = {
        "worker": getattr(args, "retry_worker", None),
        "ci": getattr(args, "retry_ci", None),
        "review": getattr(args, "retry_review", None),
        "merge": getattr(args, "retry_merge", None),
    }
    for key, value in arg_map.items():
        if value is not None:
            retry[key] = _validate_retry_value(key, value)
    if not interactive:
        return retry

    print(_copy(
        "\nFailure retry limits (0 blocks immediately):",
        "\n执行失败回退上限(0=该类失败立即 blocked):"))
    labels = {
        "worker": _copy("Worker retries after a run ends without submit (retry.worker)", "worker run 未 submit 回到 worker 次数(retry.worker)"),
        "ci": _copy("Worker retries after CI failure (retry.ci)", "CI 失败回到 worker 次数(retry.ci)"),
        "review": _copy("Worker retries after reviewer rejection (retry.review)", "reviewer reject 回到 worker 次数(retry.review)"),
        "merge": _copy("Worker retries after merge failure (retry.merge)", "merge 失败回到 worker 次数(retry.merge)"),
    }
    for key in ("worker", "ci", "review", "merge"):
        raw = _prompt(labels[key], str(retry[key]))
        try:
            retry[key] = _validate_retry_value(key, int(raw))
        except ValueError:
            raise ValidationError(_copy(
                f"retry.{key} must be a non-negative integer; got {raw!r}",
                f"retry.{key} 必须为非负整数,got {raw!r}"))
    return retry


def _select_engine(args, *, language: str) -> str:
    if args.engine:
        return args.engine
    print(f"\n{t('init.engine.options', language=language, engines=', '.join(ENGINE_TYPES))}")
    return _prompt(t("init.engine.prompt", language=language), "mock") or "mock"


def _select_language(interactive: bool) -> str:
    global _INIT_LANGUAGE
    if not interactive:
        _INIT_LANGUAGE = EN
        return EN
    _INIT_LANGUAGE = resolve_language({
        "language": _prompt("Language (en/cn)", EN).lower()})
    return _INIT_LANGUAGE


def _git_origin_url() -> Optional[str]:
    """当前目录的 git origin URL(新建 project 时默认登记到 workspace);取不到返回 None。"""
    import subprocess
    try:
        out = subprocess.run(["git", "remote", "get-url", "origin"],
                             capture_output=True, text=True)
    except FileNotFoundError:
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def _repo_name_from_url(url: str) -> str:
    base = url.rstrip("/").rsplit("/", 1)[-1]
    return base[:-4] if base.endswith(".git") else base


def _create_project_interactive(store, workspace: str, language: str = CN) -> str:
    origin = _git_origin_url()
    title = _prompt(_copy("New project title", "新 project 标题"), _repo_name_from_url(origin) if origin else None)
    if not title:
        raise ValidationError(_copy("Project title is required", "project 标题必填"))
    repo = _prompt(_copy(
        "GitHub repository URL (Enter uses the current origin)",
        "关联的 GitHub repo URL(回车用当前 origin)"), origin or "")
    from ...pipeline.dispatch import project_description
    info = store.create_project(
        workspace, title, [repo] if repo else [],
        description=project_description(language))
    tail = _copy(
        f", repositories: {', '.join(info.repos)}", f",关联 repo {', '.join(info.repos)}") if info.repos else _copy(" (no repository)", "(未关联 repo)")
    print(_copy(
        f"Project created: {info.title} ({info.id}){tail}",
        f"已新建 project:{info.title} ({info.id}){tail}"))
    return info.id


def _select_project(args, store, workspace: str, engine: str,
                    language: str = CN) -> Optional[str]:
    """multica 必须绑定一个 project(issue 归入其下,不裸建);mock 不需要,返回 None。"""
    if engine != "multica":
        return None
    if args.project:
        return args.project
    try:
        projects = store.list_projects(workspace)
    except OmacError as e:
        raise ValidationError(_copy(
            f"Could not list projects: {e}\nUse --project <id> explicitly.",
            f"无法获取 project 列表 —— {e}\n用 --project <id> 显式指定"))
    print(_copy("\nAvailable projects:", "\n可用 project:"))
    for i, p in enumerate(projects, 1):
        repo = f" [{', '.join(p.repos)}]" if p.repos else ""
        print(f"  {i}. {p.title} ({p.id}){repo}")
    print(_copy(
        "  n. Create a project and register the current repository",
        "  n. 新建 project(默认把当前 repo 登记到 workspace)"))
    raw = _prompt(_copy(
        "Choose project (number, ID, or n to create)",
        "选择 project(序号 / id / n 新建)"), "n" if not projects else None)
    if raw.lower() == "n":
        return _create_project_interactive(store, workspace, language)
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(projects):
            return projects[idx].id
    if any(p.id == raw for p in projects):
        return raw
    raise ValidationError(_copy(
        f"Project '{raw}' is not listed. Choose a number or ID, or enter n to create one.",
        f"project '{raw}' 不在列表内 —— 选序号/id,或输入 n 新建"))


def _select_workspace(args, store) -> str:
    if args.workspace:
        return args.workspace
    try:
        workspaces: List[WorkspaceInfo] = store.list_workspaces()
    except OmacError as e:
        raise ValidationError(_copy(
            f"Could not list workspaces: {e}\nUse --workspace <id> explicitly.",
            f"无法获取工作空间列表 —— {e}\n用 --workspace <id> 显式指定"))
    if not workspaces:
        return _prompt(_copy(
            "The engine returned no workspaces; enter a workspace ID",
            "引擎未返回工作空间,手动输入 workspace id"))
    print(_copy("\nAvailable workspaces:", "\n可用工作空间:"))
    for i, w in enumerate(workspaces, 1):
        desc = f" — {w.description}" if w.description else ""
        print(f"  {i}. {w.name} ({w.id}){desc}")
    raw = _prompt(_copy(
        "Choose workspace (number or ID)", "选择 workspace(序号或 id)"),
        workspaces[0].id if workspaces else None)
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(workspaces):
            return workspaces[idx].id
    if any(w.id == raw for w in workspaces):
        return raw
    raise ValidationError(_copy(
        f"Workspace '{raw}' is not listed. Available: {', '.join(w.id for w in workspaces)}",
        f"workspace '{raw}' 不在列表内,可选: {', '.join(w.id for w in workspaces)}"))


def _resolve_member(raw: str, members: List[str], role: str) -> str:
    if not raw:
        raise ValidationError(_copy(
            f"Role `{role}` requires at least one agent",
            f"角色 `{role}` 必填(至少一个)"))
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(members):
            return members[idx]
    if raw in members:
        return raw
    raise ValidationError(_copy(
        f"'{raw}' for role `{role}` is not in the agent pool. Available: {', '.join(members) or '(none)'}",
        f"角色 `{role}` 的 '{raw}' 不在 agent 池内,可选: {', '.join(members) or '(空)'}"))


def _select_members(args_val: Optional[str], members: List[str], role: str,
                    default_first: bool = False) -> List[str]:
    if args_val is not None:
        picks = _split_csv(args_val)
    else:
        print(_copy(
            f"\nAvailable agents for `{role}` (comma- or space-separated):",
            f"\n可用 agent(角色 `{role}`,逗号/空格分隔多选):"))
        for i, m in enumerate(members, 1):
            print(f"  {i}. {m}")
        dft = members[0] if (default_first and members) else None
        raw = _prompt(_copy(
            f"Choose {role} (numbers or names; comma-separated)",
            f"选择 {role}(序号或名,多选逗号分隔)"), dft)
        picks = _split_csv(raw)
    resolved = [_resolve_member(p, members, role) for p in picks]
    if not resolved:
        raise ValidationError(_copy(
            f"Choose at least one agent for role `{role}`",
            f"角色 `{role}` 至少选一个"))
    return resolved


def _select_single(args_val: Optional[str], members: List[str], role: str) -> str:
    if args_val is not None:
        return _resolve_member(args_val, members, role)
    print(_copy(
        f"\nAvailable agents for `{role}`:",
        f"\n可用 agent(角色 `{role}`):"))
    for i, m in enumerate(members, 1):
        print(f"  {i}. {m}")
    dft = members[0] if members else None
    raw = _prompt(_copy(
        f"Choose {role} (number or name)",
        f"选择 {role}(序号或名)"), dft)
    return _resolve_member(raw, members, role)


def _select_acceptor(args_val: Optional[str], members: List[str]) -> Optional[str]:
    if args_val is not None:
        if args_val.strip() == "":
            return None
        return _resolve_member(args_val, members, "acceptor")
    print(_copy(
        "\nAvailable acceptor agents (optional; Enter reuses the reviewer pool):",
        "\n可用 agent(角色 acceptor,可选,回车跳过则复用 reviewers 池):"))
    for i, m in enumerate(members, 1):
        print(f"  {i}. {m}")
    raw = _prompt(_copy(
        "Choose acceptor (number or name; optional)",
        "选择 acceptor(序号或名,可空)"), "")
    if not raw:
        return None
    return _resolve_member(raw, members, "acceptor")


def _select_workflow(interactive: bool) -> dict:
    workflow = dict(config_mod.DEFAULT_WORKFLOW)
    if not interactive:
        return workflow
    print(_copy("\nDefault workflow policy:", "\n工作流默认策略:"))
    workflow["human_in_loop"] = _prompt_bool(
        _copy(
            "Require human confirmation for design and acceptance by default",
            "默认需要 human in the loop 确认设计/验收吗"), True)
    workflow["acceptance_doc"] = _prompt_bool(
        _copy(
            "Generate an acceptance document and run final acceptance after DAG convergence",
            "默认生成验收文档并在 dag run 收敛后总控验收吗"), True)
    workflow["goal_required"] = _prompt_bool(
        _copy(
            "Require plan create to start from --goal or --goal-file by default",
            "默认要求 plan create 从 --goal/--goal-file 需求出发吗"), False)
    return workflow


def _select_template(catalog: AgentTemplateCatalog) -> str:
    template_ids = catalog.list_ids()
    print(_copy("\nAvailable Agent templates:", "\n可用 Agent 模板:"))
    for i, template_id in enumerate(template_ids, 1):
        print(f"  {i}. {template_id}")
    raw = _prompt(_copy(
        "Choose an Agent template (number or name)",
        "选择 Agent 模板(序号或名称)"), template_ids[0] if template_ids else None)
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(template_ids):
            return template_ids[idx]
    if raw in template_ids:
        return raw
    raise ValidationError(_copy(
        f"Agent template '{raw}' does not exist. Available: {', '.join(template_ids) or '(none)'}",
        f"Agent 模板 '{raw}' 不存在,可选:{', '.join(template_ids) or '(空)'}"))


def _select_runtime_target(targets: List[RuntimeTarget]) -> RuntimeTarget:
    available = [target for target in targets if target.status.lower() != "offline"]
    if not available:
        raise ValidationError(_copy(
            "No Agent Runtime is online. Run `multica runtime list`, start the Multica "
            "daemon on the target machine, and retry.",
            "没有在线 Agent Runtime —— 先运行 `multica runtime list`,"
            "在目标机器启动 Multica daemon 后重试"))
    print(_copy("\nAvailable Agent Runtimes:", "\n可用 Agent Runtime:"))
    for i, target in enumerate(available, 1):
        kind = f" [{target.type}]" if target.type else ""
        print(f"  {i}. {target.name}{kind} ({target.id}, {target.status})")
    raw = _prompt(_copy(
        "Choose Runtime (number or ID)", "选择 Runtime(序号或 id)"), "1")
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(available):
            return available[idx]
    for target in available:
        if target.id == raw:
            return target
    raise ValidationError(_copy(
        f"Runtime '{raw}' is not available. Choose: {', '.join(t.id for t in available)}",
        f"Runtime '{raw}' 不在可用列表,可选:{', '.join(t.id for t in available)}"))


def _maybe_provision_template_agents(runtime, language: str = EN) -> None:
    if not _prompt_bool(_copy(
        "Create an Agent from a built-in template", "是否通过内置模板创建 Agent"), False):
        return
    catalog = AgentTemplateCatalog(language=language)
    targets = runtime.list_targets()
    while True:
        template_id = _select_template(catalog)
        template = catalog.get(template_id)
        name = _prompt(_copy("New Agent name", "新 Agent 名称"), f"omac-{template_id}")
        if not name:
            raise ValidationError(_copy("Agent name cannot be empty", "Agent 名称不能为空"))
        target = _select_runtime_target(targets)
        created = runtime.provision_agent(AgentProvisionSpec(
            name=name,
            description=_copy(
                f"Created from the OMAC {template_id} template",
                f"由 OMAC {template_id} 模板创建"),
            instructions=template.instructions,
            runtime_id=target.id,
            skills=template.skills,
        ))
        print(_copy(
            f"Agent created: {created.name} ({created.id}), Runtime={target.name}, "
            f"Skills={len(template.skills)}",
            f"已创建 Agent:{created.name} ({created.id}),"
            f"Runtime={target.name},Skills={len(template.skills)}"))
        if not _prompt_bool(_copy(
            "Create another Agent from a template",
            "是否继续通过模板创建 Agent"), False):
            return


def _build_config(engine: str, workspace: str, project: Optional[str],
                  planner: str, orchestrator: str,
                  workers: List[str], reviewers: List[str],
                  acceptor: Optional[str], workflow: Optional[dict] = None,
                  max_parallel: Optional[int] = None,
                  retry: Optional[dict] = None, language: str = EN) -> dict:
    roles = {
        "planner": planner,
        "orchestrator": orchestrator,
        "workers": list(workers),
        "reviewers": list(reviewers),
    }
    if acceptor:
        roles["acceptor"] = acceptor
    cfg = {
        "language": resolve_language({"language": language}),
        "engine": engine,
        "workspace": workspace,
    }
    if project:
        cfg["project"] = project
    defaults = dict(config_mod.DEFAULTS)
    if max_parallel is not None:
        defaults["max_parallel"] = _validate_max_parallel(max_parallel)
    retry_cfg = dict(config_mod.DEFAULT_RETRY)
    if retry is not None:
        for key in config_mod.DEFAULT_RETRY:
            if key in retry:
                retry_cfg[key] = _validate_retry_value(key, retry[key])
    cfg.update({
        "roles": roles,
        "defaults": defaults,
        "workflow": dict(workflow or config_mod.DEFAULT_WORKFLOW),
        "retry": retry_cfg,
        "acceptance": {"max_rounds": config_mod.DEFAULT_MAX_ROUNDS},
    })
    return cfg


# ==================== 主流程 ====================

def _write_config(config: dict) -> int:
    config_mod.save_config(config)
    proj = f", project={config['project']}" if config.get("project") else ""
    language = resolve_language(config)
    print(ui(
        f"Configuration written to {config_mod.CONFIG_PATH} "
        f"(engine={config['engine']}, workspace={config['workspace']}{proj})",
        f"已写入 {config_mod.CONFIG_PATH}(engine={config['engine']}, "
        f"workspace={config['workspace']}{proj})",
        language=language,
    ))
    print(ui(
        "Next: run `omac init --check`, then start with `omac plan create`.",
        "下一步:omac init --check 体检 / omac plan create 开始拆解",
        language=language,
    ))
    return exit_codes.OK


def _run_setup(args) -> int:
    supplied = [
        args.engine, args.workspace, args.planner, args.orchestrator,
        args.workers is not None, args.reviewers is not None,
    ]
    non_interactive = all(supplied)
    if not non_interactive and not sys.stdin.isatty():
        raise ValidationError(_copy(
            "omac init is an interactive setup wizard, but stdin is not interactive.\n"
            "Agents and CI should write declarative configuration with `omac config set`, "
            "then run `omac init --check`.\nMinimum example:\n"
            "  omac config set engine mock\n"
            "  omac config set workspace mock-workspace\n"
            "  omac config set roles.planner alice\n"
            "  omac config set roles.orchestrator bob\n"
            "  omac config set roles.workers '[\"alice\"]'\n"
            "  omac config set roles.reviewers '[\"charlie\"]'\n"
            "  omac init --check",
            "omac init 是人类交互式向导,当前 stdin 非交互。\n"
            "agent/CI 请使用 `omac config set` 写入声明式配置,然后运行 `omac init --check`。\n"
            "最小示例:\n"
            "  omac config set engine mock\n"
            "  omac config set workspace mock-workspace\n"
            "  omac config set roles.planner alice\n"
            "  omac config set roles.orchestrator bob\n"
            "  omac config set roles.workers '[\"alice\"]'\n"
            "  omac config set roles.reviewers '[\"charlie\"]'\n"
            "  omac config set defaults.max_parallel 4\n"
            "  omac config set retry.worker 3\n"
            "  omac config set retry.ci 3\n"
            "  omac config set retry.review 3\n"
            "  omac config set retry.merge 3\n"
            "  omac config set workflow.human_in_loop false\n"
            "  omac config set workflow.acceptance_doc true\n"
            "  omac config set workflow.goal_required true\n"
            "  omac init --check"))
    language = _select_language(interactive=not non_interactive)
    engine = _select_engine(args, language=language)
    discovery = _build_store(engine)                      # 无 workspace,跑 list_workspaces
    workspace = _select_workspace(args, discovery)
    engine_instance = _build_engine(engine, workspace)
    store = engine_instance.store                         # 带 workspace,跑 list_members / list_projects
    project = _select_project(
        args, store, workspace, engine, language)  # multica 必选/必建;mock 返回 None
    existing_members = store.list_members(workspace)
    if not non_interactive:
        print(_copy("\nExisting Agents:", "\n当前已有 Agent:"))
        if existing_members:
            for member in existing_members:
                print(f"  - {member}")
        else:
            print(_copy("  (none)", "  (无)"))
        _maybe_provision_template_agents(engine_instance.runtime, language)
    members = store.list_members(workspace) if not non_interactive else existing_members
    if not members:
        raise ValidationError(_copy(
            f"Workspace '{workspace}' has no available agents. Add an agent on the platform and retry.",
            f"工作空间 '{workspace}' 无可用 agent —— 先在平台添加 agent 后重试"))
    planner = _select_single(args.planner, members, "planner")
    orchestrator = _select_single(args.orchestrator, members, "orchestrator")
    workers = _select_members(args.workers, members, "workers", default_first=True)
    reviewers = _select_members(args.reviewers, members, "reviewers", default_first=True)
    # 全参数非交互:acceptor 未给则缺省跳过,不弹 prompt
    if non_interactive:
        acceptor = None
        if args.acceptor and args.acceptor.strip():
            acceptor = _resolve_member(args.acceptor, members, "acceptor")
    else:
        acceptor = _select_acceptor(args.acceptor, members)
    max_parallel = _select_max_parallel(args.max_parallel, interactive=not non_interactive)
    retry = _select_retry(args, interactive=not non_interactive)
    workflow = _select_workflow(interactive=not non_interactive)
    return _write_config(_build_config(
        engine, workspace, project, planner, orchestrator, workers, reviewers,
        acceptor, workflow, max_parallel, retry, language))


# ==================== 体检 ====================

def _report(problems: List[str]) -> int:
    if problems:
        print(_copy("Health check failed:", "体检未通过:"), file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return exit_codes.VALIDATION
    cfg = config_mod.load_config()
    proj = f", project={cfg.get('project')}" if cfg.get("project") else ""
    print(_copy(
        f"Health check passed: {config_mod.CONFIG_PATH} is ready "
        f"(engine={cfg.get('engine')}, workspace={cfg.get('workspace')}{proj})",
        f"体检通过:{config_mod.CONFIG_PATH} 就绪(engine={cfg.get('engine')}, "
        f"workspace={cfg.get('workspace')}{proj})"))
    return exit_codes.OK


def _check() -> int:
    global _INIT_LANGUAGE
    problems: List[str] = []
    cfg = config_mod.load_config()
    _INIT_LANGUAGE = resolve_language(cfg)
    if not cfg:
        problems.append(_copy(
            f"Configuration file not found: {config_mod.CONFIG_PATH}.\n"
            "For initial human setup, run the interactive `omac init` wizard.\n"
            "Agents and CI should use `omac config set`, then run `omac init --check`.\n"
            "Minimum example:\n"
            "  omac config set engine mock\n"
            "  omac config set workspace mock-workspace\n"
            "  omac config set roles.planner alice\n"
            "  omac config set roles.orchestrator bob\n"
            "  omac config set roles.workers '[\"alice\"]'\n"
            "  omac config set roles.reviewers '[\"charlie\"]'\n"
            "  omac init --check",
            f"配置文件不存在: {config_mod.CONFIG_PATH}。\n"
            "人类首次配置请运行 `omac init` 交互式向导。\n"
            "agent/CI 请使用 `omac config set` 写入声明式配置,然后运行 `omac init --check`。\n"
            "最小示例:\n"
            "  omac config set engine mock\n"
            "  omac config set workspace mock-workspace\n"
            "  omac config set roles.planner alice\n"
            "  omac config set roles.orchestrator bob\n"
            "  omac config set roles.workers '[\"alice\"]'\n"
            "  omac config set roles.reviewers '[\"charlie\"]'\n"
            "  omac config set defaults.max_parallel 4\n"
            "  omac config set retry.worker 3\n"
            "  omac config set retry.ci 3\n"
            "  omac config set retry.review 3\n"
            "  omac config set retry.merge 3\n"
            "  omac config set workflow.human_in_loop false\n"
            "  omac config set workflow.acceptance_doc true\n"
            "  omac config set workflow.goal_required true\n"
            "  omac init --check"))
        return _report(problems)
    for key in ("engine", "workspace", "roles"):
        if not cfg.get(key):
            problems.append(_copy(
                f"Configuration is missing `{key}`; see `omac guide roles`.",
                f"配置缺少 `{key}` 字段(见 omac guide roles)"))
    engine_type = cfg.get("engine")
    workspace = cfg.get("workspace") or ""
    project = cfg.get("project") or ""
    roles = cfg.get("roles") or {}

    if engine_type == "multica" and not project:
        problems.append(_copy(
            "The multica engine requires `project`; every issue must belong to a project. "
            "Run `omac init` to choose or create one.",
            "multica 引擎缺少 `project` 字段(issue 必须归入一个 project)—— "
            "运行 `omac init` 选择或新建一个 project"))

    if engine_type == "multica" and shutil.which("multica") is None:
        problems.append(_copy(
            "multica CLI is not on PATH. Install it, sign in, and retry: "
            "brew install multica-ai/tap/multica && multica login",
            "multica CLI 不在 PATH —— 安装并登录后重试: "
            "brew install multica-ai/tap/multica && multica login"))

    # 引擎可达时:校验 workspace 存在 + 各角色 agent 在池内;不可达降级为本地体检
    if engine_type in ENGINE_TYPES and not problems:
        try:
            discovery = _build_store(engine_type, "")
            ws_ids = [w.id for w in discovery.list_workspaces()]
            if workspace and workspace not in ws_ids:
                problems.append(_copy(
                    f"Workspace '{workspace}' is not returned by the engine "
                    f"({', '.join(ws_ids) or 'none'}). Run `omac init` to choose again.",
                    f"workspace '{workspace}' 不在引擎返回的工作空间列表"
                    f"({', '.join(ws_ids) or '空'}) —— 运行 `omac init` 重选"))
            members_store = _build_store(engine_type, workspace)
            if engine_type == "multica" and project:
                proj_ids = [p.id for p in members_store.list_projects(workspace)]
                if project not in proj_ids:
                    problems.append(_copy(
                        f"Project '{project}' is not in the workspace project list "
                        f"({', '.join(proj_ids) or 'none'}). Run `omac init` to choose again.",
                        f"project '{project}' 不在 workspace 的 project 列表"
                        f"({', '.join(proj_ids) or '空'}) —— 运行 `omac init` 重选"))
            members = members_store.list_members(workspace) if workspace else []
            for role_name, val in roles.items():
                for agent in _as_list(val):
                    if members and agent not in members:
                        problems.append(_copy(
                            f"Agent '{agent}' for role `{role_name}` is not in the workspace pool "
                            f"(available: {', '.join(members)})",
                            f"角色 `{role_name}` 的 agent '{agent}' 不在工作空间 agent 池内"
                            f"(可选: {', '.join(members)})"))
        except OmacError as e:
            print(_copy(
                f"Warning: engine unavailable; using local checks only: {e}",
                f"警告:引擎不可达,降级为本地体检: {e}"), file=sys.stderr)

    return _report(problems)


def run(args) -> int:
    if args.check:
        return _check()
    return _run_setup(args)
