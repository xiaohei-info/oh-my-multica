"""omac init — 交互式配置 / --check 体检(设计文档 §7.1)。

两种模式:
  omac init            交互式:引擎 → workspace → 全量 agent → 角色映射 → 落盘 config.yaml
  omac init --check    体检:本地 + 引擎可达时校验 workspace 存在、各角色 agent 在池内

非交互模式(全参数直出):
  omac init --engine mock --workspace ws --planner a --orchestrator b \\
            --workers c,d --reviewers e,f [--acceptor g]

红线:init 只调引擎接口(WorkItemStore.list_workspaces / list_members),
绝不直接 shell out 平台 CLI(§12.4)。
"""
from __future__ import annotations

import shutil
import sys
from typing import List, Optional

from ...core import config as config_mod
from ...engines import ENGINE_TYPES, create_engine
from ...engines.models import EngineConfig, WorkspaceInfo
from ...errors import OmacError, ValidationError
from .. import exit_codes

NAME = "init"
SUMMARY = "交互式配置 / --check 体检"
DESCRIPTION = """一次性配置:选定 workspace → 列出全量 agent → 完成角色映射,
固化进 .omac/config.yaml(不引入小队/分组等平台特有概念)。

  omac init            交互式生成配置
  omac init --check    体检:multica CLI 是否在 PATH / 配置文件是否存在且含
                       engine·workspace·roles / 各角色 agent 是否在工作空间内

非交互模式(全参数零交互直出,适合 agent/脚本):
  omac init --engine mock --workspace ws \\
            --planner alice --orchestrator bob \\
            --workers carol,dave --reviewers eve,frank [--acceptor grace]
"""


def register(parser):
    parser.add_argument("--check", action="store_true", help="体检模式,不写任何文件")
    parser.add_argument("--engine", choices=list(ENGINE_TYPES), help="引擎类型(multica|mock)")
    parser.add_argument("--workspace", help="工作空间 id")
    parser.add_argument("--project", help="项目 id(multica 必填;交互模式可现场选择或新建)")
    parser.add_argument("--planner", help="planner agent 名")
    parser.add_argument("--orchestrator", help="orchestrator agent 名")
    parser.add_argument("--workers", help="worker agent 名,逗号分隔")
    parser.add_argument("--reviewers", help="reviewer agent 名,逗号分隔")
    parser.add_argument("--acceptor", help="acceptor agent 名(可选,缺省复用 reviewers 池)")


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


def _prompt(message: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default else ""
    raw = input(f"{message}{suffix}: ").strip()
    return raw or (default or "")


def _select_engine(args) -> str:
    if args.engine:
        return args.engine
    print("\n可选引擎:", ", ".join(ENGINE_TYPES))
    return _prompt("选择引擎", "mock") or "mock"


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


def _create_project_interactive(store, workspace: str) -> str:
    origin = _git_origin_url()
    title = _prompt("新 project 标题", _repo_name_from_url(origin) if origin else None)
    if not title:
        raise ValidationError("project 标题必填")
    repo = _prompt("关联的 GitHub repo URL(回车用当前 origin)", origin or "")
    from ...pipeline.dispatch import OMAC_PROJECT_DESCRIPTION
    info = store.create_project(
        workspace, title, [repo] if repo else [],
        description=OMAC_PROJECT_DESCRIPTION)
    tail = f",关联 repo {', '.join(info.repos)}" if info.repos else "(未关联 repo)"
    print(f"已新建 project:{info.title} ({info.id}){tail}")
    return info.id


def _select_project(args, store, workspace: str, engine: str) -> Optional[str]:
    """multica 必须绑定一个 project(issue 归入其下,不裸建);mock 不需要,返回 None。"""
    if engine != "multica":
        return None
    if args.project:
        return args.project
    try:
        projects = store.list_projects(workspace)
    except OmacError as e:
        raise ValidationError(f"无法获取 project 列表 —— {e}\n用 --project <id> 显式指定")
    print("\n可用 project:")
    for i, p in enumerate(projects, 1):
        repo = f" [{', '.join(p.repos)}]" if p.repos else ""
        print(f"  {i}. {p.title} ({p.id}){repo}")
    print("  n. 新建 project(默认把当前 repo 登记到 workspace)")
    raw = _prompt("选择 project(序号 / id / n 新建)", "n" if not projects else None)
    if raw.lower() == "n":
        return _create_project_interactive(store, workspace)
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(projects):
            return projects[idx].id
    if any(p.id == raw for p in projects):
        return raw
    raise ValidationError(f"project '{raw}' 不在列表内 —— 选序号/id,或输入 n 新建")


def _select_workspace(args, store) -> str:
    if args.workspace:
        return args.workspace
    try:
        workspaces: List[WorkspaceInfo] = store.list_workspaces()
    except OmacError as e:
        raise ValidationError(f"无法获取工作空间列表 —— {e}\n用 --workspace <id> 显式指定")
    if not workspaces:
        return _prompt("引擎未返回工作空间,手动输入 workspace id")
    print("\n可用工作空间:")
    for i, w in enumerate(workspaces, 1):
        desc = f" — {w.description}" if w.description else ""
        print(f"  {i}. {w.name} ({w.id}){desc}")
    raw = _prompt("选择 workspace(序号或 id)", workspaces[0].id if workspaces else None)
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(workspaces):
            return workspaces[idx].id
    if any(w.id == raw for w in workspaces):
        return raw
    raise ValidationError(f"workspace '{raw}' 不在列表内,可选: {', '.join(w.id for w in workspaces)}")


def _resolve_member(raw: str, members: List[str], role: str) -> str:
    if not raw:
        raise ValidationError(f"角色 `{role}` 必填(至少一个)")
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(members):
            return members[idx]
    if raw in members:
        return raw
    raise ValidationError(
        f"角色 `{role}` 的 '{raw}' 不在 agent 池内,可选: {', '.join(members) or '(空)'}")


def _select_members(args_val: Optional[str], members: List[str], role: str,
                    default_first: bool = False) -> List[str]:
    if args_val is not None:
        picks = _split_csv(args_val)
    else:
        print(f"\n可用 agent(角色 `{role}`,逗号/空格分隔多选):")
        for i, m in enumerate(members, 1):
            print(f"  {i}. {m}")
        dft = members[0] if (default_first and members) else None
        raw = _prompt(f"选择 {role}(序号或名,多选逗号分隔)", dft)
        picks = _split_csv(raw)
    resolved = [_resolve_member(p, members, role) for p in picks]
    if not resolved:
        raise ValidationError(f"角色 `{role}` 至少选一个")
    return resolved


def _select_single(args_val: Optional[str], members: List[str], role: str) -> str:
    if args_val is not None:
        return _resolve_member(args_val, members, role)
    print(f"\n可用 agent(角色 `{role}`):")
    for i, m in enumerate(members, 1):
        print(f"  {i}. {m}")
    dft = members[0] if members else None
    raw = _prompt(f"选择 {role}(序号或名)", dft)
    return _resolve_member(raw, members, role)


def _select_acceptor(args_val: Optional[str], members: List[str]) -> Optional[str]:
    if args_val is not None:
        if args_val.strip() == "":
            return None
        return _resolve_member(args_val, members, "acceptor")
    print(f"\n可用 agent(角色 acceptor,可选,回车跳过则复用 reviewers 池):")
    for i, m in enumerate(members, 1):
        print(f"  {i}. {m}")
    raw = _prompt("选择 acceptor(序号或名,可空)", "")
    if not raw:
        return None
    return _resolve_member(raw, members, "acceptor")


def _build_config(engine: str, workspace: str, project: Optional[str],
                  planner: str, orchestrator: str,
                  workers: List[str], reviewers: List[str],
                  acceptor: Optional[str]) -> dict:
    roles = {
        "planner": planner,
        "orchestrator": orchestrator,
        "workers": list(workers),
        "reviewers": list(reviewers),
    }
    if acceptor:
        roles["acceptor"] = acceptor
    cfg = {
        "engine": engine,
        "workspace": workspace,
    }
    if project:
        cfg["project"] = project
    cfg.update({
        "roles": roles,
        "defaults": dict(config_mod.DEFAULTS),
        "retry": dict(config_mod.DEFAULT_RETRY),
        "acceptance": {"max_rounds": config_mod.DEFAULT_MAX_ROUNDS},
    })
    return cfg


# ==================== 主流程 ====================

def _write_config(config: dict) -> int:
    config_mod.save_config(config)
    proj = f", project={config['project']}" if config.get("project") else ""
    print(f"已写入 {config_mod.CONFIG_PATH}(engine={config['engine']}, "
          f"workspace={config['workspace']}{proj})")
    print("下一步:omac init --check 体检 / omac plan create 开始拆解")
    return exit_codes.OK


def _run_setup(args) -> int:
    engine = _select_engine(args)
    discovery = _build_store(engine)                      # 无 workspace,跑 list_workspaces
    workspace = _select_workspace(args, discovery)
    store = _build_store(engine, workspace)               # 带 workspace,跑 list_members / list_projects
    project = _select_project(args, store, workspace, engine)  # multica 必选/必建;mock 返回 None
    members = store.list_members(workspace)
    if not members:
        raise ValidationError(
            f"工作空间 '{workspace}' 无可用 agent —— 先在平台添加 agent 后重试")
    planner = _select_single(args.planner, members, "planner")
    orchestrator = _select_single(args.orchestrator, members, "orchestrator")
    workers = _select_members(args.workers, members, "workers", default_first=True)
    reviewers = _select_members(args.reviewers, members, "reviewers", default_first=True)
    # 全参数非交互:acceptor 未给则缺省跳过,不弹 prompt
    non_interactive = all([
        args.engine, args.workspace, args.planner, args.orchestrator,
        args.workers is not None, args.reviewers is not None])
    if non_interactive:
        acceptor = None
        if args.acceptor and args.acceptor.strip():
            acceptor = _resolve_member(args.acceptor, members, "acceptor")
    else:
        acceptor = _select_acceptor(args.acceptor, members)
    return _write_config(_build_config(
        engine, workspace, project, planner, orchestrator, workers, reviewers, acceptor))


# ==================== 体检 ====================

def _report(problems: List[str]) -> int:
    if problems:
        print("体检未通过:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return exit_codes.VALIDATION
    cfg = config_mod.load_config()
    proj = f", project={cfg.get('project')}" if cfg.get("project") else ""
    print(f"体检通过:{config_mod.CONFIG_PATH} 就绪(engine={cfg.get('engine')}, "
          f"workspace={cfg.get('workspace')}{proj})")
    return exit_codes.OK


def _check() -> int:
    problems: List[str] = []
    cfg = config_mod.load_config()
    if not cfg:
        problems.append(f"配置文件不存在: {config_mod.CONFIG_PATH} —— 运行 `omac init` 生成")
        return _report(problems)
    for key in ("engine", "workspace", "roles"):
        if not cfg.get(key):
            problems.append(f"配置缺少 `{key}` 字段(见 omac guide roles)")
    engine_type = cfg.get("engine")
    workspace = cfg.get("workspace") or ""
    project = cfg.get("project") or ""
    roles = cfg.get("roles") or {}

    if engine_type == "multica" and not project:
        problems.append(
            "multica 引擎缺少 `project` 字段(issue 必须归入一个 project)—— "
            "运行 `omac init` 选择或新建一个 project")

    if engine_type == "multica" and shutil.which("multica") is None:
        problems.append("multica CLI 不在 PATH —— 安装并登录后重试: "
                        "brew install multica-ai/tap/multica && multica login")

    # 引擎可达时:校验 workspace 存在 + 各角色 agent 在池内;不可达降级为本地体检
    if engine_type in ENGINE_TYPES and not problems:
        try:
            discovery = _build_store(engine_type, "")
            ws_ids = [w.id for w in discovery.list_workspaces()]
            if workspace and workspace not in ws_ids:
                problems.append(
                    f"workspace '{workspace}' 不在引擎返回的工作空间列表"
                    f"({', '.join(ws_ids) or '空'}) —— 运行 `omac init` 重选")
            members_store = _build_store(engine_type, workspace)
            if engine_type == "multica" and project:
                proj_ids = [p.id for p in members_store.list_projects(workspace)]
                if project not in proj_ids:
                    problems.append(
                        f"project '{project}' 不在 workspace 的 project 列表"
                        f"({', '.join(proj_ids) or '空'}) —— 运行 `omac init` 重选")
            members = members_store.list_members(workspace) if workspace else []
            for role_name, val in roles.items():
                for agent in _as_list(val):
                    if members and agent not in members:
                        problems.append(
                            f"角色 `{role_name}` 的 agent '{agent}' 不在工作空间 agent 池内"
                            f"(可选: {', '.join(members)})")
        except OmacError as e:
            print(f"警告:引擎不可达,降级为本地体检: {e}", file=sys.stderr)

    return _report(problems)


def run(args) -> int:
    if args.check:
        return _check()
    return _run_setup(args)
