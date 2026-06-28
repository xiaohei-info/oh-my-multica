"""
MulticaEngine 状态映射单元测试 — 不需要真 CLI，纯函数验证。

守护 issue #232 修复的两条契约 bug：
1. _status_to_multica(FAILED) == "blocked"（不是 "failed"，真 CLI 无 failed 状态）
2. _multica_to_status("cancelled") == BLOCKED（防止已取消 issue 被当 todo 重派）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engines.multica import MulticaEngine
from engines.models import WorkItemStatus, EngineConfig

# _status_to_multica / _multica_to_status 是实例方法，用 dummy config 建实例
_dummy = MulticaEngine(EngineConfig(engine_type="multica", workspace_id="dummy"))


def test_status_to_multica_failed_maps_to_blocked():
    """FAILED -> "blocked"，不是 "failed"（真 CLI 合法值无 failed）。"""
    assert _dummy._status_to_multica(WorkItemStatus.FAILED) == "blocked"


def test_multica_to_status_cancelled_maps_to_blocked():
    """cancelled -> BLOCKED，不是默认 TODO（防止已取消 issue 被重派）。"""
    assert _dummy._multica_to_status("cancelled") == WorkItemStatus.BLOCKED


def test_status_to_multica_full_table():
    """全状态正向映射对照真 CLI 合法值。"""
    assert _dummy._status_to_multica(WorkItemStatus.TODO) == "todo"
    assert _dummy._status_to_multica(WorkItemStatus.IN_PROGRESS) == "in_progress"
    assert _dummy._status_to_multica(WorkItemStatus.IN_REVIEW) == "in_review"
    assert _dummy._status_to_multica(WorkItemStatus.DONE) == "done"
    assert _dummy._status_to_multica(WorkItemStatus.BLOCKED) == "blocked"


def test_multica_to_status_full_table():
    """全状态反向映射。"""
    assert _dummy._multica_to_status("todo") == WorkItemStatus.TODO
    assert _dummy._multica_to_status("in_progress") == WorkItemStatus.IN_PROGRESS
    assert _dummy._multica_to_status("in_review") == WorkItemStatus.IN_REVIEW
    assert _dummy._multica_to_status("done") == WorkItemStatus.DONE
    assert _dummy._multica_to_status("blocked") == WorkItemStatus.BLOCKED
    assert _dummy._multica_to_status("cancelled") == WorkItemStatus.BLOCKED


# ==================== list_work_items 分页 + 服务端过滤 ====================
# 守护 list_work_items 两条契约：
# 1. 服务端单页上限 100，必须用 --offset 翻页拿全集（旧 `--limit 1000` 会被静默截断）。
# 2. status 给定时把 `--status <multica态>` 下推到服务端，减少传输量。

def _paged_engine(pages):
    """构造一个 _run_multica 被打桩、按 --offset 返回预置分页的 engine。

    pages: List[List[dict]]，第 i 段是第 i 页（offset = i*100）返回的 issues。
    记录每次调用的 args 到 engine._calls 供断言。
    """
    eng = MulticaEngine(EngineConfig(engine_type="multica", workspace_id="ws"))
    eng._calls = []

    def fake_run(args, capture=True):
        eng._calls.append(args)
        # 从 args 里取 --offset
        offset = 0
        if "--offset" in args:
            offset = int(args[args.index("--offset") + 1])
        idx = offset // MulticaEngine._LIST_PAGE_SIZE
        page = pages[idx] if idx < len(pages) else []
        return {"issues": page}

    eng._run_multica = fake_run
    return eng


def _issue(n, status="todo"):
    return {"id": f"id{n}", "title": f"t{n}", "status": status, "metadata": {}}


def test_list_work_items_paginates_past_100():
    """两满页 + 一不足页 -> 拿回全部 250 条，不被首页 100 截断。"""
    pages = [
        [_issue(i) for i in range(100)],          # offset 0
        [_issue(i) for i in range(100, 200)],     # offset 100
        [_issue(i) for i in range(200, 250)],     # offset 200（不足页，终止）
    ]
    eng = _paged_engine(pages)
    items = eng.list_work_items("ws")
    assert len(items) == 250, f"应翻页拿全 250 条，实际 {len(items)}"
    # 第三页不足 100 即停，不应再请求 offset 250
    offsets = [c[c.index("--offset") + 1] for c in eng._calls if "--offset" in c]
    assert offsets == ["0", "100", "200"], f"翻页 offset 序列异常: {offsets}"


def test_list_work_items_single_short_page_stops():
    """首页就不足一整页 -> 只请求一次，不空翻下一页。"""
    eng = _paged_engine([[_issue(1), _issue(2)]])
    items = eng.list_work_items("ws")
    assert len(items) == 2
    assert len(eng._calls) == 1, f"短页应只请求一次，实际 {len(eng._calls)}"


def test_list_work_items_pushes_status_filter_server_side():
    """status 给定 -> 命令行带 `--status <multica态>`，且客户端按业务态精确收口。"""
    eng = _paged_engine([[_issue(1, "done"), _issue(2, "in_progress")]])
    items = eng.list_work_items("ws", status=WorkItemStatus.DONE)
    # 服务端过滤参数已下推
    assert "--status" in eng._calls[0]
    assert eng._calls[0][eng._calls[0].index("--status") + 1] == "done"
    # 客户端精确收口：只保留业务态 DONE
    assert [it.id for it in items] == ["id1"]


def test_list_work_items_no_status_omits_filter_flag():
    """status 缺省 -> 不带 --status，拉全集。"""
    eng = _paged_engine([[_issue(1)]])
    eng.list_work_items("ws")
    assert "--status" not in eng._calls[0]
