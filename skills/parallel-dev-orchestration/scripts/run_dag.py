#!/usr/bin/env python3
"""
DAG 编排引擎 - manifest 驱动

主循环结构（sync -> decide -> dispatch）：
  1. SYNC:    harvest 在飞节点终态 + worker/reviewer 阶段过渡（查平台 -> 写回 manifest）
  2. DECIDE:  读 manifest 算 frontier + 失败隔离（下游标 blocked）
  3. DISPATCH: fire-and-forget 派发所有 ready 节点（建 work item -> assign -> 标 in_progress，不阻塞）
  4. 终止:    无 ready 且无在飞 -> 带报告退出
"""
import sys
import time
import argparse
from pathlib import Path
from typing import Set

sys.path.insert(0, str(Path(__file__).parent))

from core import load_manifest, save_manifest, set_node, lint, frontier, downstream_of
from core.evidence import validate_worker_evidence, validate_review_evidence
from utils import commit_manifest, git_sync_enabled

from engines import (
    create_engine_from_env,
    CollaborationEngine,
    WorkItemStatus,
)

# manifest 中的终态值
TERMINAL_STATUSES = {"done", "blocked", "failed"}
INFLIGHT_STATUSES = {"in_progress", "in_review"}

REVIEW_APPROVE = {"pass", "pass-with-nits"}


# ==================== reconcile ====================

def reconcile(engine: CollaborationEngine, manifest, manifest_path: str):
    """启动校验：逐节点拿 work_item_id 去平台核对真实状态，全量同步回 manifest。

    - 有 work_item_id -> get_work_item(work_item_id) 精准取：
      平台状态与 manifest 不一致 -> 以平台为准写回 manifest（含 in_progress/in_review）；
      work_item_id 指向的 item 在平台已不存在 -> 清空 work_item_id 走新建。
    - 无 work_item_id -> 该节点未建，留待 execute_dag 首次建。
    """
    changed = False
    for key, node in manifest.nodes.items():
        if not node.work_item_id:
            continue
        try:
            item = engine.get_work_item(node.work_item_id)
        except Exception:
            print(f"  reconcile: {key} work_item_id={node.work_item_id} 平台不存在，清空待新建")
            set_node(manifest, key, work_item_id=None, status="todo")
            changed = True
            continue

        platform_status = item.status.value
        if platform_status != node.status:
            print(f"  reconcile: {key} manifest={node.status} -> 平台={platform_status}，同步")
            set_node(manifest, key, status=platform_status)
            changed = True

    if changed:
        save_manifest(manifest, manifest_path)


# ==================== 核心执行逻辑 ====================

def _harvest(
    engine: CollaborationEngine,
    manifest,
    manifest_path: str,
    completed: Set[str],
    failed: Set[str],
) -> bool:
    """SYNC 阶段：收割在飞节点的终态 + worker→reviewer 阶段过渡。

    in_progress 节点：
      平台 DONE + 有 PR 产物 -> 有 reviewer 则 assign reviewer + 转 in_review，
      无 reviewer 直接标 done + completed.add
      平台 DONE 缺 PR 产物 / FAILED -> 标 blocked + failed.add

    in_review 节点：
      平台有 review_verdict -> pass/pass-with-nits 标 done + completed.add，
      其他 verdict 标 blocked + failed.add
    """
    changed = False
    pending_review = []  # reviewer 过渡（遍历后执行，避免改 manifest 影响遍历）

    for key, node in manifest.nodes.items():
        if node.status not in INFLIGHT_STATUSES or not node.work_item_id:
            continue
        try:
            item = engine.get_work_item(node.work_item_id)
        except Exception:
            continue

        # ---- in_progress: worker 完成 -> done 或 reviewer 过渡 ----
        if node.status == "in_progress":
            if item.status == WorkItemStatus.DONE:
                reviewer = getattr(node, "reviewer", None)
                gate_errors = validate_worker_evidence(node, item)
                if gate_errors:
                    print(f"  harvest: {key} worker evidence gate failed -> blocked: {'; '.join(gate_errors)}")
                    engine.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                    set_node(manifest, key, status="blocked")
                    failed.add(key)
                elif reviewer:
                    print(f"  harvest: {key} worker done，过渡到 reviewer {reviewer}")
                    pending_review.append((key, node.work_item_id, reviewer))
                else:
                    print(f"  harvest: {key} -> done")
                    set_node(manifest, key, status="done")
                    completed.add(key)
                changed = True
            elif item.status == WorkItemStatus.FAILED:
                print(f"  harvest: {key} worker failed -> blocked")
                engine.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                set_node(manifest, key, status="blocked")
                failed.add(key)
                changed = True
            elif item.status == WorkItemStatus.BLOCKED:
                print(f"  harvest: {key} worker blocked on platform -> blocked")
                set_node(manifest, key, status="blocked")
                failed.add(key)
                changed = True

        # ---- in_review: reviewer 完成 -> done 或 blocked ----
        elif node.status == "in_review":
            verdict = item.review_verdict
            if not verdict:
                continue
            gate_errors = validate_review_evidence(node, item)
            if not gate_errors:
                print(f"  harvest: {key} reviewer approved -> done")
                engine.update_status(node.work_item_id, WorkItemStatus.DONE)
                set_node(manifest, key, status="done")
                completed.add(key)
            else:
                print(f"  harvest: {key} reviewer evidence gate failed ({verdict}) -> blocked: {'; '.join(gate_errors)}")
                engine.update_status(node.work_item_id, WorkItemStatus.BLOCKED)
                set_node(manifest, key, status="blocked")
                failed.add(key)
            changed = True

    # ---- reviewer 过渡（遍历后执行）----
    for key, item_id, reviewer in pending_review:
        engine.assign_work_item(item_id, reviewer, "reviewer")
        engine.update_status(item_id, WorkItemStatus.IN_REVIEW)
        set_node(manifest, key, status="in_review")

    if changed:
        save_manifest(manifest, manifest_path)
        commit_manifest(manifest_path, "harvest: sync in-flight")

    return changed


def _build_snapshot(manifest) -> dict:
    """DECIDE 阶段：从 manifest 构建 snapshot（不查平台，manifest 是唯一口径）。"""
    snapshot = {}
    for key, node in manifest.nodes.items():
        snapshot[key] = {
            "id": node.work_item_id or key,
            "status": node.status,
            "worker": node.worker,
            "reviewer": node.reviewer,
            "blocked_by": node.blocked_by,
        }
    return snapshot


def _mark_blocked(manifest, manifest_path, failed: Set[str]) -> Set[str]:
    """将失败节点的下游标记为 blocked（因上游失败/被拒，不再派发）。返回 blocked 集合。"""
    snapshot = _build_snapshot(manifest)
    downstream = downstream_of(snapshot, failed)
    blocked = set()
    for key in downstream:
        if manifest.nodes[key].status not in TERMINAL_STATUSES:
            set_node(manifest, key, status="blocked")
            blocked.add(key)
    if blocked:
        save_manifest(manifest, manifest_path)
        print(f"  失败隔离: {sorted(blocked)} 标记为 blocked")
    return blocked


def execute_dag(
    engine: CollaborationEngine,
    manifest,
    manifest_path: str,
    max_parallel: int = 4,
):
    """执行 DAG 编排循环 - sync -> decide -> dispatch。

    SYNC:    harvest 在飞节点终态 + worker/reviewer 阶段过渡
    DECIDE:  读 manifest 算 frontier + 失败隔离（下游标 blocked）
    DISPATCH: fire-and-forget 派发所有 ready 节点（受 max_parallel 约束，不阻塞）
    终止:    无 ready 且无在飞 -> 带报告退出
    """
    squad_id = engine.config.squad_id or engine.config.workspace_id

    completed = {k for k, n in manifest.nodes.items() if n.status == "done"}
    failed = {k for k, n in manifest.nodes.items() if n.status in ("blocked", "failed")}

    # 上一轮 blocked/failed 的节点重置为 todo 以便重试
    # （上游修好后重跑，被阻塞的下游自动恢复；若上游仍失败，_mark_blocked 下轮会重新标）
    for key, node in manifest.nodes.items():
        if node.status in ("blocked", "failed") and key not in completed:
            set_node(manifest, key, status="todo")
            failed.discard(key)
    save_manifest(manifest, manifest_path)

    if completed:
        print(f"  复用已完成节点: {sorted(completed)}")

    total = len(manifest.nodes)
    print(f"\n=== 开始执行 DAG ===")
    print(f"  总任务数: {total}（待执行 {total - len(completed)}）")
    print(f"  并发上限: {max_parallel}")

    while True:
        # ---- SYNC: harvest 在飞节点终态 ----
        _harvest(engine, manifest, manifest_path, completed, failed)

        # ---- DECIDE: 读 manifest 算 frontier + 失败隔离 ----
        if failed:
            _mark_blocked(manifest, manifest_path, failed)

        snapshot = _build_snapshot(manifest)
        ready = [k for k in frontier(snapshot)
                 if k not in failed and k not in completed]

        # ---- 终止判定：无 ready 且无在飞 ----
        in_flight = {k for k, n in manifest.nodes.items()
                     if n.status in INFLIGHT_STATUSES}
        if not ready and not in_flight:
            break

        if not ready:
            print(f"  等待在飞节点完成（{len(in_flight)} 个在飞），{engine.config.polling_interval}s 后重试...")
            time.sleep(engine.config.polling_interval)
            continue

        # ---- DISPATCH: fire-and-forget 派发所有 ready 节点（受 max_parallel 约束）----
        in_flight_count = len(in_flight)
        slots = max(0, max_parallel - in_flight_count)
        to_dispatch = ready[:slots]

        if not to_dispatch:
            print(f"  并发已满（{in_flight_count}/{max_parallel}），等待在飞节点完成...")
            time.sleep(engine.config.polling_interval)
            continue

        for key in to_dispatch:
            node = manifest.nodes[key]
            worker = node.worker

            print(f"\n> 派发任务: {key}（worker: {worker}）")

            # 建工单（若无）
            if not node.work_item_id:
                item = engine.create_work_item(
                    workspace_id=squad_id,
                    title=node.title or key,
                    description=node.description or f"Task {key}",
                    dag_key=key,
                    worker=worker,
                    reviewer=getattr(node, "reviewer", None),
                    blocked_by=node.blocked_by,
                )
                if hasattr(engine, "set_node_contract") and getattr(node, "contract", None) is not None:
                    engine.set_node_contract(item.id, node.contract)
                set_node(manifest, key, work_item_id=item.id)
                print(f"  建 work item {item.id} for {key}")

            # fire-and-forget: assign worker + 标 in_progress（不轮询）
            engine.assign_work_item(node.work_item_id, worker, "worker")
            engine.update_status(node.work_item_id, WorkItemStatus.IN_PROGRESS)
            set_node(manifest, key, status="in_progress")

        save_manifest(manifest, manifest_path)
        commit_manifest(manifest_path, f"dispatch: {', '.join(to_dispatch)}")

        # 不阻塞等结果：下一轮 harvest 会收割
        time.sleep(engine.config.polling_interval)

    # 最终汇总
    print(f"\n=== DAG 执行完成 ===")
    print(f"  完成: {len(completed)}/{total}")
    print(f"  失败: {len(failed)}/{total}")
    done_pct = len(completed) / total * 100 if total > 0 else 0
    print(f"  成功率: {done_pct:.1f}%")

    save_manifest(manifest, manifest_path)
    commit_manifest(manifest_path, f"DAG execution complete: {len(completed)}/{total} done")


# ==================== 主流程 ====================

def start_new_run(manifest_path: str, engine: CollaborationEngine = None, max_parallel: int = 4,
                  *, engine_type: str = None, workspace_id: str = None):
    """启动新的编排：load -> lint -> reconcile -> execute_dag。

    engine 为 None 时按「环境变量为唯一面」解析配置（.env 可选 + 命令行覆盖）。
    """
    print(f"=== 加载 manifest: {manifest_path} ===")
    manifest = load_manifest(manifest_path)

    if engine is None:
        print("=== 初始化引擎 ===")
        engine = create_engine_from_env(engine_type=engine_type, workspace_id=workspace_id)

    # squad 优先级：manifest.meta.squad 优先，缺失则回退到 env 来的 config.squad_id（MULTICA_SQUAD_ID）。
    # 二者皆无才报错——消除「manifest 硬必填 squad」这个特殊情况，让 clone → setup(env) → run
    # 这条 onboarding 路径成立（manifest 由 orchestrator 自动生成，clone 时尚不存在，无法预先编辑）。
    squad_id = manifest.meta.get("squad") or engine.config.squad_id
    if not squad_id:
        print("错误: 未提供派发小队 squad —— 在 manifest.meta.squad 指定，或设置 MULTICA_SQUAD_ID 环境变量")
        sys.exit(1)

    print(f"  引擎类型: {engine.__class__.__name__}")
    print(f"  工作空间: {engine.config.workspace_id}")
    print(f"  小队: {squad_id}")
    print(f"  轮询间隔: {engine.config.polling_interval}s")
    print(f"  git 回写: {'开（ORCH_GIT_SYNC）' if git_sync_enabled() else '关（默认，仅本地写文件，不 commit/push）'}")

    engine.config.squad_id = squad_id

    # 从引擎配置读 max_parallel（覆盖默认）
    mp = engine.config.extra.get("MAX_PARALLEL")
    if mp:
        try:
            max_parallel = int(mp)
        except (ValueError, TypeError):
            pass

    # Lint
    print("=== Lint manifest ===")
    members = engine.list_members(squad_id)
    errors = lint(manifest, members)
    if errors:
        print("Lint 失败:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print("Lint 通过")

    # Reconcile（全量同步平台状态）
    print(f"\n=== Reconcile ===")
    reconcile(engine, manifest, manifest_path)

    # 执行 DAG
    execute_dag(engine, manifest, manifest_path, max_parallel=max_parallel)

    # 最终状态
    total = len(manifest.nodes)
    done_count = sum(1 for n in manifest.nodes.values() if n.status == "done")
    failed_count = sum(1 for n in manifest.nodes.values() if n.status in ("blocked", "failed"))
    print(f"\n=== 最终状态 ===")
    print(f"  完成: {done_count}/{total}")
    print(f"  失败: {failed_count}/{total}")


def main():
    parser = argparse.ArgumentParser(description="DAG 编排引擎 - manifest 驱动")
    parser.add_argument("manifest", nargs="?", help="manifest 文件路径")
    parser.add_argument("--engine", help="引擎类型（默认从 .env 读取）")
    parser.add_argument("--workspace", help="工作空间 ID（默认从 manifest 读取）")
    parser.add_argument("--max-parallel", type=int, default=4,
                        help="最大并发派发数（默认 4）")

    args = parser.parse_args()

    if not args.manifest:
        parser.print_help()
        sys.exit(1)

    # 配置统一走 create_engine_from_env：.env(可选) < 进程环境 < 命令行参数
    start_new_run(
        args.manifest,
        max_parallel=args.max_parallel,
        engine_type=args.engine,
        workspace_id=args.workspace,
    )


if __name__ == "__main__":
    main()
