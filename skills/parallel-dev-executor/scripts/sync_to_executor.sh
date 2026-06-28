#!/usr/bin/env bash
# 把 orchestration/scripts（权威源）整体镜像到 executor/scripts，保持两个 skill 的脚本逐字一致。
#
# 为什么：Multica 按 agent 隔离物化 skill——worker/reviewer 只拿到 executor skill，不会带出
# orchestration。所以 executor 必须自带完整引擎层（engines/ + core/ + agent_cli.py …）。
# 开发口径：只在 orchestration 改脚本，改完跑本脚本同步；CI 用 test_skill_scripts_parity 拦未同步。
#
# 无论从哪个副本运行，始终以 orchestration 为源、executor 为目标。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"            # scripts → <skill> → skills → repo 根
SRC="$ROOT/skills/parallel-dev-orchestration/scripts"
DST="$ROOT/skills/parallel-dev-executor/scripts"

if [ ! -d "$SRC" ]; then
  echo "找不到权威源: $SRC" >&2
  exit 1
fi

mkdir -p "$DST"
rsync -a --delete \
  --exclude='__pycache__' --exclude='.pytest_cache' --exclude='*.pyc' \
  "$SRC/" "$DST/"

echo "synced: $SRC -> $DST"
