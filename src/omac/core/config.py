"""omac 项目配置(.omac/config.yaml)。

设计文档 §6:配置与状态一律 YAML 进 git,不用 SQLite。
优先级:config.yaml < 环境变量(OMAC_*)< 命令行参数。

结构(全部键可选,init 交互式生成):
    engine: multica | mock
    workspace: <id>
    project: <id>                # multica 必填:issue 归入的 project(repo 在 workspace registry)
    roles:
      planner / orchestrator: <agent>
      workers / reviewers: [<agent>, ...]
      acceptor: <agent>          # 可选,缺省复用 reviewers 池
    defaults:
      max_parallel / poll_interval / coverage_gate
    workflow:
      human_in_loop: true       # plan 设计/验收产出后是否默认等人确认
      review: true              # plan/decompose 是否默认走 reviewer 门
      acceptance_doc: true      # plan create 是否默认生成验收文档
      goal_required: false      # 无 --doc 时是否强制 --goal/--goal-file
    ci:    { check_command, timeout_minutes }   # 可选;未显式配置时检测 .github/workflows
    merge: { command }                           # 可选;未显式配置时默认 gh pr merge
    delivery: { review_before_pr: false,      # 可选;开启后先评审 branch+tip,评审 pass 再发 draft PR
                external_merge: false }       # 可选;开启后 OMAC 绝不自行 merge,只等待并校验外部 merge 证据
    machine: { project, namespace }           # 可选;机器隔离,须成对设置(缺省无隔离)
    acceptance: { max_rounds }                   # 总控验收外层循环上限(与 retry 正交)
    retry:                                     # 各类「回到 worker」回退次数上限
      worker: 3                                # worker run 结束但未 submit → worker 继续处理
      ci: 3                                    # CI 失败 → worker 重修(0 = 立即 blocked,不回退)
      review: 3                                # reviewer reject → worker 重修(节点开发与 plan 流水线共用)
      merge: 3                                 # 合并冲突 → worker 重解
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from ..errors import ValidationError
from ..i18n import ui

CONFIG_DIR = ".omac"
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")

DEFAULTS = {
    "max_parallel": 4,
    "poll_interval": 30,
    "coverage_gate": 90,
}

DEFAULT_WORKFLOW = {
    "human_in_loop": True,
    "review": True,
    "acceptance_doc": True,
    "goal_required": False,
}

# 各类「回到 worker」回退次数上限(设计文档 §6 / §7.3;缺省 3,0 = 该类失败即 blocked)
DEFAULT_RETRY = {
    "worker": 3,
    "ci": 3,
    "review": 3,
    "merge": 3,
}

# 交付排序特性门(全部缺省关闭 —— 上游默认行为不变)
DEFAULT_DELIVERY = {
    "review_before_pr": False,
    "external_merge": False,
}

# PlanReturn 人工计划门(缺省:无受信 host —— fail closed;快照存入 .omac/plans)
DEFAULT_PLAN_GATE = {
    "store_dir": os.path.join(CONFIG_DIR, "plans"),
    "allowed_hosts": [],
}

# 机器隔离(缺省:不配置 —— project/namespace 均为 None,无机器隔离)
DEFAULT_MACHINE = {
    "project": None,
    "namespace": None,
}

# 总控验收外层循环上限(设计文档 §6;与 retry 正交)
DEFAULT_MAX_ROUNDS = 3

DEFAULT_GITHUB_CHECK_COMMAND = "gh pr checks {pr_url} --watch --fail-fast"
DEFAULT_GITHUB_MERGE_COMMAND = "gh pr merge {pr_url} --squash --delete-branch"
DEFAULT_MOCK_MERGE_COMMAND = "true"


# 环境变量回退(设计文档 §5:全局 flag 带 env 回退)
ENV_ENGINE = "OMAC_ENGINE"
ENV_WORKSPACE = "OMAC_WORKSPACE_ID"
ENV_PROJECT = "OMAC_PROJECT_ID"


def load_config(path: str = CONFIG_PATH) -> dict:
    """读配置文件;不存在返回空 dict(命令自行决定缺配置时的行为)。"""
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValidationError(ui(
            f"Invalid configuration format; expected a YAML mapping: {path}",
            f"配置文件格式错误(应为 YAML 映射): {path}"))
    return data


def save_config(data: dict, path: str = CONFIG_PATH):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def get_value(data: dict, dotted_key: str):
    """按点分路径取值:get_value(cfg, "roles.planner")。不存在返回 None。"""
    cur = data
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def set_value(data: dict, dotted_key: str, value):
    """按点分路径写值,中间层不存在则创建。"""
    parts = dotted_key.split(".")
    cur = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def resolve_retry(config: dict) -> dict:
    """解析 retry 块:以 DEFAULT_RETRY 为缺省,合并 config.retry,并校验。

    校验规则(设计文档 §6):retry.{worker|ci|review|merge} 必须为整数且 ≥ 0;
    负数在「校验期」报错(ValidationError → exit 5)。
    """
    raw = get_value(config, "retry")
    if raw is None:
        return dict(DEFAULT_RETRY)
    if not isinstance(raw, dict):
        raise ValidationError(ui(
            f"retry must be a YAML mapping (worker/ci/review/merge); got {type(raw).__name__}",
            f"retry 配置应为 YAML 映射(ci/review/merge),got {type(raw).__name__}"))
    resolved = dict(DEFAULT_RETRY)
    for key in DEFAULT_RETRY:
        if key not in raw:
            continue
        val = raw[key]
        if isinstance(val, bool) or not isinstance(val, int):
            raise ValidationError(ui(
                f"retry.{key} must be an integer; got {type(val).__name__}({val!r})",
                f"retry.{key} 必须为整数,got {type(val).__name__}({val!r})"))
        if val < 0:
            raise ValidationError(ui(
                f"retry.{key} cannot be negative; got {val}, expected ≥ 0",
                f"retry.{key} 不能为负数(非法值 {val});需 ≥ 0"))
        resolved[key] = val
    return resolved


def resolve_workflow(config: dict) -> dict:
    """解析 workflow 块:项目级流程策略,缺省保持历史行为。"""
    raw = get_value(config, "workflow")
    if raw is None:
        return dict(DEFAULT_WORKFLOW)
    if not isinstance(raw, dict):
        raise ValidationError(ui(
            f"workflow must be a YAML mapping; got {type(raw).__name__}",
            f"workflow 配置应为 YAML 映射,got {type(raw).__name__}"))
    resolved = dict(DEFAULT_WORKFLOW)
    for key in DEFAULT_WORKFLOW:
        if key not in raw:
            continue
        val = raw[key]
        if not isinstance(val, bool):
            raise ValidationError(ui(
                f"workflow.{key} must be true or false; got {type(val).__name__}({val!r})",
                f"workflow.{key} 必须为布尔值 true/false,got {type(val).__name__}({val!r})"))
        resolved[key] = val
    return resolved


def resolve_delivery(config: dict) -> dict:
    """解析 delivery 块:交付排序特性门,缺省全关(上游默认行为不变)。

    review_before_pr:开启后 worker 以 branch + 精确 tip_sha 交付(无 pr_url),
    评审 pass 后由 pipeline 确定性发布 draft PR,再走 CI → merge 门。
    external_merge:开启后 OMAC 绝不执行 merge 命令;进入结构化等待态,
    只接受绑定到已批准 pr_url + tip 的外部 merge 证据(artifacts.external_merge),
    stale/wrong/畸形证据一律拒绝(blocked + 报错即教学)。
    校验规则与 resolve_workflow 一致:值必须为布尔,非法值 ValidationError → exit 5。
    """
    raw = get_value(config, "delivery")
    if raw is None:
        return dict(DEFAULT_DELIVERY)
    if not isinstance(raw, dict):
        raise ValidationError(ui(
            f"delivery must be a YAML mapping; got {type(raw).__name__}",
            f"delivery 配置应为 YAML 映射,got {type(raw).__name__}"))
    resolved = dict(DEFAULT_DELIVERY)
    for key in DEFAULT_DELIVERY:
        if key not in raw:
            continue
        val = raw[key]
        if not isinstance(val, bool):
            raise ValidationError(ui(
                f"delivery.{key} must be true or false; got {type(val).__name__}({val!r})",
                f"delivery.{key} 必须为布尔值 true/false,got {type(val).__name__}({val!r})"))
        resolved[key] = val
    return resolved


def resolve_plan_gate(config: dict) -> dict:
    """解析 plan_gate 块:PlanReturn 人工计划门的快照目录与受信 host。

    store_dir 必须为字符串;allowed_hosts 必须为字符串列表(缺省空 ——
    host 形式 fail closed,必须显式配置才可用)。非法值 ValidationError → exit 5。
    """
    raw = get_value(config, "plan_gate")
    if raw is None:
        return dict(DEFAULT_PLAN_GATE)
    if not isinstance(raw, dict):
        raise ValidationError(ui(
            f"plan_gate must be a YAML mapping (store_dir/allowed_hosts); got {type(raw).__name__}",
            f"plan_gate 配置应为 YAML 映射(store_dir/allowed_hosts),got {type(raw).__name__}"))
    resolved = dict(DEFAULT_PLAN_GATE)
    if "store_dir" in raw:
        val = raw["store_dir"]
        if not isinstance(val, str) or not val:
            raise ValidationError(ui(
                f"plan_gate.store_dir must be a non-empty string; got {type(val).__name__}({val!r})",
                f"plan_gate.store_dir 必须为非空字符串,got {type(val).__name__}({val!r})"))
        resolved["store_dir"] = val
    if "allowed_hosts" in raw:
        val = raw["allowed_hosts"]
        if not isinstance(val, list) or any(not isinstance(h, str) or not h for h in val):
            raise ValidationError(ui(
                f"plan_gate.allowed_hosts must be a list of non-empty strings; got {val!r}",
                f"plan_gate.allowed_hosts 必须为非空字符串列表,got {val!r}"))
        resolved["allowed_hosts"] = list(val)
    return resolved


def resolve_machine(config: dict) -> dict:
    """解析 machine 块:机器隔离的 project/namespace 指针。

    缺省(块不存在)→ DEFAULT_MACHINE(两者皆 None,无机器隔离)。
    project/namespace 设置时必须为非空字符串;隔离要求成对出现 ——
    只设其一 → ValidationError(exit 5)。非法值同样 ValidationError → exit 5。
    """
    raw = get_value(config, "machine")
    if raw is None:
        return dict(DEFAULT_MACHINE)
    if not isinstance(raw, dict):
        raise ValidationError(ui(
            f"machine must be a YAML mapping (project/namespace); got {type(raw).__name__}",
            f"machine 配置应为 YAML 映射(project/namespace),got {type(raw).__name__}"))
    resolved = dict(DEFAULT_MACHINE)
    for key in DEFAULT_MACHINE:
        if key not in raw:
            continue
        val = raw[key]
        if not isinstance(val, str) or not val:
            raise ValidationError(ui(
                f"machine.{key} must be a non-empty string; got {type(val).__name__}({val!r})",
                f"machine.{key} 必须为非空字符串,got {type(val).__name__}({val!r})"))
        resolved[key] = val
    if (resolved["project"] is None) != (resolved["namespace"] is None):
        raise ValidationError(ui(
            "machine isolation requires both project and namespace; "
            f"got project={resolved['project']!r}, namespace={resolved['namespace']!r}",
            "机器隔离必须同时设置 project 与 namespace;"
            f"got project={resolved['project']!r},namespace={resolved['namespace']!r}"))
    return resolved


def resolve_engine_settings(
    config: dict, *, engine: str | None = None,
    workspace: str | None = None, project: str | None = None,
) -> tuple[str, str, str | None]:
    """按「config.yaml < env < 命令行参数」解析 (engine_type, workspace_id, project_id)。

    engine / workspace 必须有值,否则 ValidationError(报错即教学:告知三种给法)。
    project 是 **multica 引擎的必填项**(issue 必须归入一个 project,不 fallback 到
    workspace 裸建):multica 下缺 project 即 ValidationError → exit 5;
    mock 引擎不要求 project(返回 None)。
    """
    engine_type = engine or os.environ.get(ENV_ENGINE) or config.get("engine")
    workspace_id = workspace or os.environ.get(ENV_WORKSPACE) or config.get("workspace")
    project_id = project or os.environ.get(ENV_PROJECT) or config.get("project")
    if not engine_type:
        raise ValidationError(ui(
            "Engine type is missing. Set config.yaml engine, "
            f"environment variable {ENV_ENGINE}, or --engine.",
            "未指定引擎类型 —— 三种给法任选:config.yaml 的 engine 字段 / "
            f"环境变量 {ENV_ENGINE} / 命令行 --engine"))
    if not workspace_id:
        raise ValidationError(ui(
            "Workspace is missing. Set config.yaml workspace, "
            f"environment variable {ENV_WORKSPACE}, or --workspace.",
            "未指定 workspace —— 三种给法任选:config.yaml 的 workspace 字段 / "
            f"环境变量 {ENV_WORKSPACE} / 命令行 --workspace"))
    if engine_type == "multica" and not project_id:
        raise ValidationError(ui(
            "The multica engine requires a project. Set config.yaml project, "
            f"environment variable {ENV_PROJECT}, or --project; or run `omac init` "
            "to choose or create one.",
            "multica 引擎必须指定 project(issue 归入该 project,不裸建于 workspace)"
            " —— 三种给法任选:config.yaml 的 project 字段 / "
            f"环境变量 {ENV_PROJECT} / 命令行 --project;"
            "或运行 `omac init` 选择已有 project / 新建一个(自动登记当前 repo 到 workspace)"))
    return engine_type, workspace_id, project_id


def _has_github_workflow(root: str | os.PathLike = ".") -> bool:
    workflows = Path(root) / ".github" / "workflows"
    if not workflows.is_dir():
        return False
    return any(workflows.glob("*.yml")) or any(workflows.glob("*.yaml"))


def get_ci_config(config: dict, root: str | os.PathLike = ".") -> dict | None:
    """返回 ci 配置块;未显式配置时按 .github/workflows 自动判断。

    设计文档 §6/§7.3:ci 可选。显式 check_command 最高优先级;未配置时,
    若仓库存在 GitHub Actions workflow,默认用 gh pr checks;否则跳过 CI。
    check_command 是带 {pr_url} 占位的模板命令,subprocess 执行,退出码即结论。
    """
    ci = config.get("ci")
    if isinstance(ci, dict) and ci.get("check_command"):
        return ci
    if not _has_github_workflow(root):
        return None
    timeout = ci.get("timeout_minutes", 30) if isinstance(ci, dict) else 30
    return {
        "check_command": DEFAULT_GITHUB_CHECK_COMMAND,
        "timeout_minutes": timeout,
    }


def get_merge_config(config: dict) -> dict | None:
    """返回 merge 配置块;未显式配置时默认用 gh pr merge。

    设计文档 §7.3:reviewer pass 后应进入自动合并门。显式 command 最高优先级;
    未配置或 command 为空时使用 GitHub CLI 默认命令。退出码即结论。
    """
    merge = config.get("merge")
    if isinstance(merge, dict) and merge.get("command"):
        return merge
    timeout = merge.get("timeout_minutes", 30) if isinstance(merge, dict) else 30
    command = DEFAULT_MOCK_MERGE_COMMAND if config.get("engine") == "mock" \
        else DEFAULT_GITHUB_MERGE_COMMAND
    return {"command": command, "timeout_minutes": timeout}
