#!/usr/bin/env python3
"""
引擎接口测试脚本

验证 9 个核心接口是否正常工作
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engines import create_engine_from_config, WorkItemStatus


def run_engine_interface_test(engine_type: str, workspace_id: str):
    """测试引擎的核心接口"""
    print(f"\n{'='*60}")
    print(f"测试 {engine_type.upper()} 引擎")
    print(f"{'='*60}\n")

    # 1. 创建引擎
    print("1️⃣ 创建引擎...")
    engine = create_engine_from_config(engine_type, workspace_id)
    print(f"   ✅ 引擎类型: {engine.__class__.__name__}")
    print(f"   ✅ 工作空间: {workspace_id}")
    print(f"   ✅ 推荐轮询间隔: {engine.get_recommended_polling_interval()}s")

    # 2. 列出成员
    print("\n2️⃣ 列出成员...")
    members = engine.list_members(workspace_id)
    print(f"   ✅ 成员数量: {len(members)}")
    print(f"   ✅ 成员列表: {members[:5]}")  # 只显示前 5 个

    if not members:
        print("   ⚠️  警告: 工作空间没有成员，跳过后续测试")
        return

    # 3. 创建工作单元
    print("\n3️⃣ 创建工作单元...")
    worker = members[0]
    reviewer = members[1] if len(members) > 1 else None

    work_item = engine.create_work_item(
        workspace_id=workspace_id,
        title="测试任务",
        description="这是一个测试任务，用于验证引擎接口",
        dag_key="test-task-001",
        worker=worker,
        reviewer=reviewer,
        blocked_by=["dependency-task"],
        wave=0,
        initial_status=WorkItemStatus.TODO
    )
    print(f"   ✅ 任务 ID: {work_item.id}")
    print(f"   ✅ 标题: {work_item.title}")
    print(f"   ✅ Worker: {work_item.worker}")
    print(f"   ✅ Reviewer: {work_item.reviewer}")
    print(f"   ✅ 状态: {work_item.status.value}")
    print(f"   ✅ DAG Key: {work_item.dag_key}")

    item_id = work_item.id

    # 4. 获取工作单元
    print("\n4️⃣ 获取工作单元详情...")
    retrieved_item = engine.get_work_item(item_id)
    print(f"   ✅ 标题: {retrieved_item.title}")
    print(f"   ✅ 状态: {retrieved_item.status.value}")

    # 5. 按 DAG key 查找
    print("\n5️⃣ 按 work_item_id 精准取...")
    retrieved_by_id = engine.get_work_item(item_id)
    if retrieved_by_id and retrieved_by_id.dag_key == "test-task-001":
        print(f"   ✅ 精准取到任务: {retrieved_by_id.id}")
    else:
        print(f"   ❌ 精准取失败")

    # 6. 更新状态
    print("\n6️⃣ 更新任务状态...")
    engine.update_status(item_id, WorkItemStatus.IN_PROGRESS)
    updated_item = engine.get_work_item(item_id)
    print(f"   ✅ 新状态: {updated_item.status.value}")

    # 7. 更新元数据
    print("\n7️⃣ 更新任务元数据...")
    engine.update_work_item_metadata(
        item_id,
        artifacts={"pr": "https://github.com/owner/repo/pull/123"},
        review_verdict="pass",
        review_comment="LGTM"
    )
    updated_item = engine.get_work_item(item_id)
    print(f"   ✅ 产物: {updated_item.artifacts}")
    print(f"   ✅ 审核结果: {updated_item.review_verdict}")

    # 8. 添加评论
    print("\n8️⃣ 添加评论...")
    engine.add_comment(item_id, "这是一条测试评论")
    print(f"   ✅ 评论已添加")

    # 9. 重新分配任务
    print("\n9️⃣ 重新分配任务...")
    new_assignee = members[1] if len(members) > 1 else members[0]
    engine.assign_work_item(item_id, new_assignee, "worker")
    updated_item = engine.get_work_item(item_id)
    print(f"   ✅ 新 Worker: {updated_item.worker}")

    # 10. 列出工作单元
    print("\n🔟 列出工作单元...")
    all_items = engine.list_work_items(workspace_id)
    print(f"   ✅ 总任务数: {len(all_items)}")

    in_progress_items = engine.list_work_items(workspace_id, status=WorkItemStatus.IN_PROGRESS)
    print(f"   ✅ 进行中任务数: {len(in_progress_items)}")

    # 11. 标记完成
    print("\n1️⃣1️⃣ 标记任务完成...")
    engine.mark_done(item_id)
    final_item = engine.get_work_item(item_id)
    print(f"   ✅ 最终状态: {final_item.status.value}")

    print(f"\n{'='*60}")
    print(f"✅ {engine_type.upper()} 引擎测试完成")
    print(f"{'='*60}\n")


def main():
    """主函数"""
    print("\n" + "="*60)
    print("🚀 引擎接口测试")
    print("="*60)

    # 测试 Mock 引擎（不需要外部依赖）
    print("\n📝 测试 1: Mock 引擎")
    try:
        test_engine("mock", "test-workspace")
    except Exception as e:
        print(f"\n❌ Mock 引擎测试失败: {e}")
        import traceback
        traceback.print_exc()

    # 可选：测试 Multica 引擎（需要真实环境）
    if len(sys.argv) > 1 and sys.argv[1] == "--multica":
        print("\n📝 测试 2: Multica 引擎")
        workspace_id = input("请输入 Multica workspace ID: ").strip()
        if workspace_id:
            try:
                test_engine("multica", workspace_id)
            except Exception as e:
                print(f"\n❌ Multica 引擎测试失败: {e}")
                import traceback
                traceback.print_exc()

    # 可选：测试 GitHub 引擎（需要真实环境）
    if len(sys.argv) > 1 and sys.argv[1] == "--github":
        print("\n📝 测试 3: GitHub 引擎")
        repo = input("请输入 GitHub repo (owner/repo): ").strip()
        token = input("请输入 GitHub token (可选): ").strip()
        if repo:
            try:
                engine = create_engine_from_config(
                    "github",
                    repo,
                    GITHUB_TOKEN=token
                )
                test_engine("github", repo)
            except Exception as e:
                print(f"\n❌ GitHub 引擎测试失败: {e}")
                import traceback
                traceback.print_exc()

    print("\n" + "="*60)
    print("✅ 所有测试完成")
    print("="*60)
    print("\n提示:")
    print("  - 运行 `python test_engines.py --multica` 测试 Multica 引擎")
    print("  - 运行 `python test_engines.py --github` 测试 GitHub 引擎")
    print()


if __name__ == '__main__':
    main()
