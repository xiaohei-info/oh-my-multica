"""omac config — 读写 .omac/config.yaml(点分路径)。骨架期即可用。"""
from __future__ import annotations

import yaml

from ...core import config as config_mod
from ...errors import ValidationError
from ...i18n import current_language, resolve_language, t, ui
from .. import exit_codes
from ..output import add_output_flag, print_json

NAME = "config"
SUMMARY = "读写项目配置(.omac/config.yaml)"
DESCRIPTION = """读写项目配置,键用点分路径。

  omac config get                     # 输出整份配置
  omac config get roles.planner
  omac config set defaults.max_parallel 8
  omac config set roles.workers '["backend-agent","fe-agent"]'   # JSON/YAML 均可

值按 YAML 解析(数字、布尔、列表自动识别),敏感信息(token)不应也不需要
写进配置——认证由各平台 CLI 自管。
"""


def register(parser):
    language = resolve_language(config_mod.load_config())
    sub = parser.add_subparsers(dest="action", metavar="<action>", required=True)
    get = sub.add_parser("get", help="读配置(整份或单键)")
    get.add_argument("key", nargs="?", help=t("config.help.key", language=language))
    add_output_flag(get)

    set_p = sub.add_parser("set", help="写单键")
    set_p.add_argument("key", help=t("config.help.set_key", language=language))
    set_p.add_argument("value", help=t("config.help.value", language=language))


def run(args) -> int:
    if args.action == "get":
        cfg = config_mod.load_config()
        if not cfg:
            raise ValidationError(ui(
                f"Configuration file not found: {config_mod.CONFIG_PATH}. Run `omac init` first.",
                f"配置文件不存在: {config_mod.CONFIG_PATH} —— 先运行 `omac init`"))
        data = cfg if not args.key else config_mod.get_value(cfg, args.key)
        if args.key and data is None:
            raise ValidationError(ui(
                f"Configuration key not found: {args.key}",
                f"配置中不存在键: {args.key}"))
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
    print(ui(
        f"{args.key} = {value!r} written to {config_mod.CONFIG_PATH}",
        f"{args.key} = {value!r} 已写入 {config_mod.CONFIG_PATH}",
        language=current_language(),
    ))
    return exit_codes.OK


def _validate_set_value(key: str, value):
    """写入前校验:把明显非法配置挡在 config set 阶段。"""
    if key == "language":
        resolve_language({"language": value})
        return

    if key.startswith("retry."):
        sub = key.split(".", 1)[1]
        if sub not in config_mod.DEFAULT_RETRY:
            return
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError(ui(
                f"{key} must be an integer; got {type(value).__name__}({value!r})",
                f"{key} 必须为整数,got {type(value).__name__}({value!r})"))
        if value < 0:
            raise ValidationError(ui(
                f"{key} cannot be negative; got {value}, expected ≥ 0",
                f"{key} 不能为负数(非法值 {value});需 ≥ 0"))
        return

    if key.startswith("workflow."):
        sub = key.split(".", 1)[1]
        if sub not in config_mod.DEFAULT_WORKFLOW:
            return
        if not isinstance(value, bool):
            raise ValidationError(ui(
                f"{key} must be true or false; got {type(value).__name__}({value!r})",
                f"{key} 必须为布尔值 true/false,got {type(value).__name__}({value!r})"))
