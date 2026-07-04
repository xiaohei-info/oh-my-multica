"""omac config — 读写 .orchestrator/config.yaml(点分路径)。骨架期即可用。"""
from __future__ import annotations

import yaml

from ...core import config as config_mod
from ...errors import ValidationError
from .. import exit_codes
from ..output import add_output_flag, print_json

NAME = "config"
SUMMARY = "读写项目配置(.orchestrator/config.yaml)"
DESCRIPTION = """读写项目配置,键用点分路径。

  omac config get                     # 输出整份配置
  omac config get roles.planner
  omac config set defaults.max_parallel 8
  omac config set roles.workers '["backend-agent","fe-agent"]'   # JSON/YAML 均可

值按 YAML 解析(数字、布尔、列表自动识别),敏感信息(token)不应也不需要
写进配置——认证由各平台 CLI 自管。
"""


def register(parser):
    sub = parser.add_subparsers(dest="action", metavar="<action>", required=True)
    get = sub.add_parser("get", help="读配置(整份或单键)")
    get.add_argument("key", nargs="?", help="点分路径,如 roles.planner;缺省输出整份")
    add_output_flag(get)

    set_p = sub.add_parser("set", help="写单键")
    set_p.add_argument("key", help="点分路径")
    set_p.add_argument("value", help="值(按 YAML 解析)")


def run(args) -> int:
    if args.action == "get":
        cfg = config_mod.load_config()
        if not cfg:
            raise ValidationError(
                f"配置文件不存在: {config_mod.CONFIG_PATH} —— 先运行 `omac init`")
        data = cfg if not args.key else config_mod.get_value(cfg, args.key)
        if args.key and data is None:
            raise ValidationError(f"配置中不存在键: {args.key}")
        if args.output == "json":
            print_json(data)
        else:
            print(yaml.dump(data, default_flow_style=False, allow_unicode=True,
                            sort_keys=False).rstrip()
                  if isinstance(data, (dict, list)) else data)
        return exit_codes.OK

    # set
    cfg = config_mod.load_config()
    try:
        value = yaml.safe_load(args.value)
    except yaml.YAMLError:
        value = args.value
    _validate_set_value(args.key, value)
    config_mod.set_value(cfg, args.key, value)
    config_mod.save_config(cfg)
    print(f"{args.key} = {value!r} 已写入 {config_mod.CONFIG_PATH}")
    return exit_codes.OK


def _validate_set_value(key: str, value):
    """写入前校验:retry.* 必须为整数且 ≥ 0(负数 → ValidationError ≡ exit 5)。"""
    if not key.startswith("retry."):
        return
    sub = key.split(".", 1)[1]
    if sub not in config_mod.DEFAULT_RETRY:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"{key} 必须为整数,got {type(value).__name__}({value!r})")
    if value < 0:
        raise ValidationError(f"{key} 不能为负数(非法值 {value});需 ≥ 0")
