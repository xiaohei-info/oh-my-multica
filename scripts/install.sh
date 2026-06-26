#!/usr/bin/env bash
# ============================================================
# parallel-dev-skills 安装脚本
# 把两个 skill 安装进目标 Agent 的 skills 目录（复制或软链）。
# ============================================================
#
# 用法：
#   ./scripts/install.sh [TARGET_SKILLS_DIR] [--link]
#
#   TARGET_SKILLS_DIR  目标 skills 目录，默认 ~/.claude/skills
#                      常见值：
#                        Claude Code (用户级)  ~/.claude/skills
#                        Claude Code (项目级)  <project>/.claude/skills
#                        其它 Agent            见各自文档（README 有说明）
#   --link             用软链接代替复制（便于跟随仓库更新；默认复制）
#
# 示例：
#   ./scripts/install.sh                          # 复制到 ~/.claude/skills
#   ./scripts/install.sh ~/.codex/skills          # 复制到 codex 目录
#   ./scripts/install.sh ./.claude/skills --link  # 软链到当前项目
# ------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO_ROOT/skills"

TARGET="${HOME}/.claude/skills"
MODE="copy"
for arg in "$@"; do
  case "$arg" in
    --link) MODE="link" ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) TARGET="$arg" ;;
  esac
done

mkdir -p "$TARGET"
echo "源:   $SRC"
echo "目标: $TARGET"
echo "模式: $MODE"
echo

for skill in parallel-dev-orchestration parallel-dev-executor; do
  dest="$TARGET/$skill"
  if [ -e "$dest" ] || [ -L "$dest" ]; then
    echo "  跳过已存在: $dest（先手动删除以重装）"
    continue
  fi
  if [ "$MODE" = "link" ]; then
    ln -s "$SRC/$skill" "$dest"
    echo "  软链: $dest -> $SRC/$skill"
  else
    cp -r "$SRC/$skill" "$dest"
    echo "  复制: $dest"
  fi
done

echo
echo "完成。下一步："
echo "  1) 配置编排引擎：cd \"$TARGET/parallel-dev-orchestration\" && python3 scripts/setup.py"
echo "  2) 在你的 Agent 里加载 skill 'parallel-dev-orchestration' 开始编排。"
