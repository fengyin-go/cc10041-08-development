"""分区覆盖清单 + 历史回填计划的行为测试。

场景：查 2026-11-04 ~ 2026-11-07 的订单。
- 4 日同时有日分区（120 元，含补录）和月归档（结月 100 元），日分区优先；
- 5 日没有日分区，月归档 30 元；
- 6/7 日两份来源都有且金额一致（10 / 20 元）。

回填运行到 4 日写入后停用月归档，再用原清单续做：
已确认日期保留、待处理日期继续、不可读来源单独列出；恢复归档后逐日核对来源、
金额和累计写入次数，4 日只能保留一份结果。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

backend_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_root))

from app.engine.partition_coverage import coverage_engine, reset_plans
from app.seed import SEED_ROWS
from app.store import store


def reload_store():
    store._tables = {
        name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
    }
    reset_plans()


def find_partition(name: str) -> dict:
    matched = [row for row in store.rows("data_partition") if row.get("分区名称") == name]
    assert matched, f"找不到分区 {name}"
    return matched[0]


@pytest.fixture(autouse=True)
def _reset():
    reload_store()
    yield
    reload_store()


def test_coverage_reports_both_sources_and_selected_version():
    manifest = coverage_engine().preview(
        dataset="订单", start="2026-11-04", end="2026-11-07")

    by_date = {day["date"]: day for day in manifest["dates"]}
    assert set(by_date) == {"2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07"}
    assert manifest["uncovered_dates"] == []
    assert set(manifest["overlap_dates"]) == {"2026-11-04", "2026-11-06", "2026-11-07"}

    day4 = by_date["2026-11-04"]
    assert len(day4["candidates"]) == 2
    assert day4["selected_source_type"] == "日分区"
    assert day4["selected_amount"] == 120.0
    sources = {(c["source_type"], c["amount"], c["version"]) for c in day4["candidates"]}
    assert ("日分区", 120.0, "v2") in sources
    assert ("月归档", 100.0, "m1") in sources

    day5 = by_date["2026-11-05"]
    assert [c["source_type"] for c in day5["candidates"]] == ["月归档"]
    assert day5["selected_amount"] == 30.0

    for day in ("2026-11-06", "2026-11-07"):
        assert len(by_date[day]["candidates"]) == 2
        assert by_date[day]["selected_source_type"] == "日分区"

    assert manifest["planned_total_amount"] == 180.0  # 120 + 30 + 10 + 20


def test_coverage_lists_uncovered_dates():
    manifest = coverage_engine().preview(
        dataset="订单", start="2026-11-04", end="2026-11-09")
    assert manifest["uncovered_dates"] == ["2026-11-08", "2026-11-09"]
    assert manifest["selected_count"] == 4


def test_coverage_rejects_invalid_range():
    with pytest.raises(ValueError):
        coverage_engine().preview(dataset="订单", start="2026-11-07", end="2026-11-04")
    with pytest.raises(ValueError):
        coverage_engine().preview(dataset="订单", start="2026/11/04", end="2026-11-07")


def test_plan_is_reused_for_same_range():
    engine = coverage_engine()
    first, created_first = engine.create_plan(
        dataset="订单", start="2026-11-04", end="2026-11-07")
    second, created_second = engine.create_plan(
        dataset="订单", start="2026-11-04", end="2026-11-07")

    assert created_first is True
    assert created_second is False
    assert first["plan_id"] == second["plan_id"]
    assert second["total_write_count"] == 0
    assert second["planned_total_amount"] == 180.0


def test_plan_reads_only_planned_range():
    engine = coverage_engine()
    plan, _ = engine.create_plan(dataset="订单", start="2026-11-05", end="2026-11-06")
    report = engine.run_plan(plan["plan_id"])

    assert report["confirmed_dates"] == ["2026-11-05", "2026-11-06"]
    assert report["total_written_amount"] == 40.0  # 30 + 10
    assert [entry["date"] for entry in report["write_log"]] == ["2026-11-05", "2026-11-06"]


def test_backfill_survives_archive_disable_and_resumes():
    engine = coverage_engine()
    plan, _ = engine.create_plan(dataset="订单", start="2026-11-04", end="2026-11-07")
    plan_id = plan["plan_id"]

    # 只跑 4 日，模拟任务还没跑完就中断。
    partial = engine.run_plan(plan_id, limit=1, reason="首轮")
    assert partial["confirmed_dates"] == ["2026-11-04"]
    assert partial["total_written_amount"] == 120.0
    assert partial["pending_dates"] == ["2026-11-05", "2026-11-06", "2026-11-07"]

    # 停用月归档，然后用原清单续做：5 日计划读的月归档不可读，回填停在这一天，
    # 6/7 日保持待处理，不会偷偷改读别的来源。
    find_partition("订单月归档-2026-11")["status"] = "已停用"
    resumed = engine.run_plan(plan_id, reason="归档停用后续做")

    assert resumed["confirmed_dates"] == ["2026-11-04"]
    assert resumed["pending_dates"] == ["2026-11-06", "2026-11-07"]
    assert [item["date"] for item in resumed["unreadable"]] == ["2026-11-05"]
    assert resumed["unreadable"][0]["partition_name"] == "订单月归档-2026-11"
    assert resumed["unreadable"][0]["current_partition_status"] == "已停用"
    assert resumed["status"] == "已阻塞"
    assert resumed["total_written_amount"] == 120.0
    assert resumed["runs"][-1]["stopped_at"] == "2026-11-05"
    # 4 日仍然只有一份结果，不能因为两个来源叠加成 220。
    day4 = next(d for d in resumed["per_day"] if d["date"] == "2026-11-04")
    assert day4["write_count"] == 1
    assert day4["actual_amount"] == 120.0

    # 也可以显式跳过不可读日期，后面能读的日期继续写完。
    skipped = engine.run_plan(
        plan_id, reason="跳过不可读日期", continue_on_unreadable=True)
    assert skipped["confirmed_dates"] == [
        "2026-11-04", "2026-11-06", "2026-11-07"]
    assert skipped["pending_dates"] == []
    assert [item["date"] for item in skipped["unreadable"]] == ["2026-11-05"]
    assert skipped["total_written_amount"] == 150.0

    # 恢复归档，再续做：只剩 5 日待写。
    find_partition("订单月归档-2026-11")["status"] = "已启用"
    final = engine.run_plan(plan_id, reason="归档恢复后续做")

    assert final["status"] == "已完成"
    assert final["confirmed_dates"] == [
        "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07"]
    assert final["total_written_amount"] == 180.0
    assert final["total_write_count"] == 4

    per_day = {item["date"]: item for item in final["per_day"]}
    assert per_day["2026-11-04"]["actual_source_type"] == "日分区"
    assert per_day["2026-11-04"]["actual_amount"] == 120.0
    assert per_day["2026-11-04"]["write_count"] == 1
    assert per_day["2026-11-05"]["actual_source_type"] == "月归档"
    assert per_day["2026-11-05"]["actual_amount"] == 30.0
    assert per_day["2026-11-06"]["actual_amount"] == 10.0
    assert per_day["2026-11-07"]["actual_amount"] == 20.0
    assert all(item["write_count"] == 1 for item in per_day.values())

    # 再跑一次不会重复写入。
    again = engine.run_plan(plan_id, reason="重复续做")
    assert again["total_write_count"] == 4
    assert again["already_complete"] is True


def test_plan_freezes_versions_after_source_change():
    engine = coverage_engine()
    plan, _ = engine.create_plan(dataset="订单", start="2026-11-04", end="2026-11-07")
    archive = find_partition("订单月归档-2026-11")
    archive["日明细"]["2026-11-05"] = 999.0

    report = engine.run_plan(plan["plan_id"])
    day5 = next(d for d in report["per_day"] if d["date"] == "2026-11-05")
    assert day5["actual_amount"] == 30.0
    assert report["total_written_amount"] == 180.0

    frozen = engine.manifest_for_plan(plan["plan_id"])
    frozen_day5 = next(d for d in frozen["dates"] if d["date"] == "2026-11-05")
    assert frozen_day5["selected_amount"] == 30.0


def test_backfill_task_tracks_plan_progress():
    engine = coverage_engine()
    plan, _ = engine.create_plan(dataset="订单", start="2026-11-04", end="2026-11-07")
    task = store.find("backfill_handler", int(plan["task_id"]))
    assert task is not None
    assert task["status"] == "待调度"

    engine.run_plan(plan["plan_id"])
    task = store.find("backfill_handler", int(plan["task_id"]))
    assert task["status"] == "已完成"
    assert "4/4" in task["处理进度"]


def test_api_endpoints_resolve_before_generic_resource_route():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    response = client.get(
        "/api/partitions/coverage",
        params={"dataset": "订单", "start": "2026-11-04", "end": "2026-11-09"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["uncovered_dates"] == ["2026-11-08", "2026-11-09"]

    created = client.post(
        "/api/partitions/backfill-plans",
        params={"dataset": "订单", "start": "2026-11-04", "end": "2026-11-07"},
    )
    assert created.status_code == 200, created.text
    plan_id = created.json()["plan_id"]

    run = client.post(f"/api/partitions/backfill-plans/{plan_id}/run")
    assert run.status_code == 200, run.text
    assert run.json()["counts"]["confirmed"] == 4
