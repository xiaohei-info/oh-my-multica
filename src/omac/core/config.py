"""omac 项目配置(.orchestrator/config.yaml)。

设计文档 §6:配置与状态一律 YAML 进 git,不用 SQLite。
优先级:config.yaml < 环境变量(OMAC_*)< 命令行参数。

结构(全部键可选,init 交互式生成):
    engine: multica | mock
    workspace: <id>
    roles:
      planner / orchestrator: <agent>
      workers / reviewers: [<agent>, ...]
      acceptor: <agent>          # 可选,缺省复用 reviewers 池
    defaults:
      max_parallel / poll_interval / coverage_gate
    ci:    { check_command, timeout_minutes }   # 可选,缺省跳过 CI 环节
    merge: { command }                           # 可选,缺省不自动合并
    acceptance: { max_rounds }                   # 总控验收外层循环上限(与 retry 正交)
    retry:                                     # 三类「回到 worker」回退次数上限
      ci: 3                                    # CI 失败 → worker 重修(0 = 立即 blocked,不回退)
      review: 3                                # reviewer reject → worker 重修(节点开发与 plan 流水线共用)
      merge: 3                                 # 合并冲突 → worker 重解
"""
from __future__ import annotations

import os

import yaml

from ..errors import ValidationError

CONFIG_DIR = ".orchestrator"
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")

DEFAULTS = {
    "max_parallel": 4,
    "poll_interval": 30,
    "coverage_gate": 90,
}

# 三类「回到 worker」回退次数上限(设计文档 §6 / §7.3;缺省 3,0 = 该类失败即 blocked)
DEFAULT_RETRY = {
    "ci": 3,
    "review": 3,
    "merge": 3,
}

# 总控验收外层循环上限(设计文档 §6;与 retry 正交)
DEFAULT_MAX_ROUNDS = 3


# 环境变量回退(设计文档 §5:全局 flag 带 env 回退)
ENV_ENGINE = "OMAC_ENGINE"
ENV_WORKSPACE = "OMAC_WORKSPACE_ID"


def load_config(path: str = CONFIG_PATH) -> dict:
    """读配置文件;不存在返回空 dict(命令自行决定缺配置时的行为)。"""
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValidationError(f"配置文件格式错误(应为 YAML 映射): {path}")
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

    校验规则(设计文档 §6):retry.{ci|review|merge} 必须为整数且 ≥ 0;
    负数在「校验期」报错(ValidationError → exit 5)。
    """
    raw = get_value(config, "retry")
    if raw is None:
        return dict(DEFAULT_RETRY)
    if not isinstance(raw, dict):
        raise ValidationError(
            f"retry 配置应为 YAML 映射(ci/review/merge),got {type(raw).__name__}")
    resolved = dict(DEFAULT_RETRY)
    for key in DEFAULT_RETRY:
        if key not in raw:
            continue
        val = raw[key]
        if isinstance(val, bool) or not isinstance(val, int):
            raise ValidationError(
                f"retry.{key} 必须为整数,got {type(val).__name__}({val!r})")
        if val < 0:
            raise ValidationError(f"retry.{key} 不能为负数(非法值 {val});需 ≥ 0")
        resolved[key] = val
    return resolved


def resolve_engine_settings(config: dict, *, engine: str | None = None,
                            workspace: str | None = None) -> tuple[str, str]:
    """按「config.yaml < env < 命令行参数」解析 (engine_type, workspace_id)。

    两者最终都必须有值,否则 ValidationError(报错即教学:告知三种给法)。
    """
    engine_type = engine or os.environ.get(ENV_ENGINE) or config.get("engine")
    workspace_id = workspace or os.environ.get(ENV_WORKSPACE) or config.get("workspace")
    if not engine_type:
        raise ValidationError(
            "未指定引擎类型 —— 三种给法任选:config.yaml 的 engine 字段 / "
            f"环境变量 {ENV_ENGINE} / 命令行 --engine")
    if not workspace_id:
        raise ValidationError(
            "未指定 workspace —— 三种给法任选:config.yaml 的 workspace 字段 / "
            f"环境变量 {ENV_WORKSPACE} / 命令行 --workspace")
    return engine_type, workspace_id


def get_ci_config(config: dict) -> dict | None:
    """返回 ci 配置块;未配置或缺少 check_command 时返回 None(环节整体跳过)。

    设计文档 §6/§7.3:ci 可选,缺省跳过 CI 环节。check_command 是带 {pr_url}
    占位的模板命令,subprocess 执行,退出码即结论。timeout_minutes 缺省 30。
    """
    ci = config.get("ci")
    if not isinstance(ci, dict) or not ci.get("check_command"):
        return None
    return ci


def get_merge_config(config: dict) -> dict | None:
    """返回 merge 配置块;未配置或缺少 command 时返回 None(不自动合并)。"""
    merge = config.get("merge")
    if not isinstance(merge, dict) or not merge.get("command"):
        return None
    return merge
