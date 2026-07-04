"""omac plan — 计划制定 + DAG 拆解流水线(全程内置 review 阶段)。"""
from __future__ import annotations

from ...core import config as config_mod
from ._stub import not_implemented


def resolve_review_rounds(cfg: dict | None = None) -> int:
    """plan 流水线评审修订轮次上界,与 dag run 节点评审共用 config.retry.review。

    设计文档 §7.2:每个 LLM 环节的修订循环有界(评审轮次读 config.retry.review,缺省 ≤3),
    耗尽则 exit 20 移交调用者。此处统一从 config.retry 读取,消除第二处硬编码。
    """
    cfg = cfg if cfg is not None else config_mod.load_config()
    retry = config_mod.resolve_retry(cfg)
    return int(retry["review"])


NAME = "plan"
SUMMARY = "计划制定 + DAG 拆解流水线(全程内置 review 阶段)"
DESCRIPTION = """计划制定与 DAG 拆解流水线。

子命令:
  create   两种模式一条流水线:
             --doc <设计文档>  跳过 planner 制定计划环节,直接进验收文档 + 拆解
             (无 --doc)      planner 从零制定计划,评审通过后继续
           计划定稿后 planner 产出验收文档(业务流程 → 用户视角端到端可执行
           验收动作),再由 orchestrator 拆解为 manifest DAG。
           issue 的范围 = 一个完整阶段:产出 → 评审 → 回退修订都在同一条
           issue 上,评审 = 该 issue 转派 reviewer。
           开关:--no-review 跳过全部 review 阶段;--no-acceptance 跳过验收文档。
  check    调用者自己拆好的 manifest:只走 lint 门 + manifest review 阶段
  show     查看已注册 manifest 的摘要

产出:.orchestrator/<name>.yaml(manifest)与 .orchestrator/<name>.acceptance.yaml
退出码:0 就绪 / 5 校验失败 / 20 修订循环耗尽,需调用者决策
"""


def register(parser):
    sub = parser.add_subparsers(dest="action", metavar="<action>", required=True)
    create = sub.add_parser("create", help="启动计划→验收文档→拆解流水线")
    create.add_argument("--name", required=True, help="manifest 名(落盘 .orchestrator/<name>.yaml)")
    create.add_argument("--doc", help="已有设计/计划文档路径(给了就跳过 planner 制定环节)")
    create.add_argument("--no-review", action="store_true", help="跳过全部 review 阶段")
    create.add_argument("--no-acceptance", action="store_true", help="跳过验收文档环节")

    check = sub.add_parser("check", help="lint + review 一份现成 manifest")
    check.add_argument("manifest", help="manifest 文件路径")

    show = sub.add_parser("show", help="查看 manifest 摘要")
    show.add_argument("manifest", help="manifest 文件路径")


def run(args) -> int:
    return not_implemented(f"plan {args.action}", "P3")
