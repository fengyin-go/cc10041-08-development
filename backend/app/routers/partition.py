"""分区覆盖清单与历史回填计划 API。

- GET  /api/partitions/coverage        按日期范围查询已核准来源和计划读取版本
- POST /api/partitions/backfill-plans  用覆盖清单创建（或复用）回填计划
- GET  /api/partitions/backfill-plans  列出所有回填计划
- GET  /api/partitions/backfill-plans/{plan_id}            计划核对报告
- GET  /api/partitions/backfill-plans/{plan_id}/manifest   计划冻结的覆盖清单
- POST /api/partitions/backfill-plans/{plan_id}/run        执行 / 续做计划
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.engine.partition_coverage import coverage_engine

router = APIRouter(prefix="/api/partitions", tags=["分区覆盖与回填"])


def _engine():
    return coverage_engine()


@router.get("/coverage")
def get_coverage(
    dataset: str = Query(..., description="数据集，例如 订单"),
    start: str = Query(..., description="开始日期 YYYY-MM-DD"),
    end: str = Query(..., description="结束日期 YYYY-MM-DD"),
    policy: str = Query(default="daily_first", description="来源优先级"),
) -> dict[str, Any]:
    """按回填日期范围列出分区覆盖清单：候选来源、本次读取版本、未覆盖日期。"""
    try:
        return _engine().preview(dataset=dataset, start=start, end=end, policy=policy)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/backfill-plans")
def create_backfill_plan(
    dataset: str = Query(..., description="数据集"),
    start: str = Query(..., description="开始日期 YYYY-MM-DD"),
    end: str = Query(..., description="结束日期 YYYY-MM-DD"),
    policy: str = Query(default="daily_first"),
    reuse: bool = Query(default=True, description="相同范围重复提交时复用已有计划"),
) -> dict[str, Any]:
    """用覆盖清单准备回填计划；同一范围默认复用，不会重新叠加金额。"""
    try:
        report, created = _engine().create_plan(
            dataset=dataset, start=start, end=end, policy=policy, reuse=reuse)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"created": created, **report}


@router.get("/backfill-plans")
def list_backfill_plans() -> dict[str, Any]:
    """列出当前所有回填计划及其状态。"""
    plans = _engine().list_plans()
    return {"items": plans, "total": len(plans)}


@router.get("/backfill-plans/{plan_id}")
def get_backfill_plan(plan_id: str) -> dict[str, Any]:
    """查看计划核对报告：已确认、待处理、不可读、未覆盖日期和实际来源。"""
    try:
        return _engine().report(plan_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/backfill-plans/{plan_id}/manifest")
def get_backfill_manifest(plan_id: str) -> dict[str, Any]:
    """查看计划冻结的覆盖清单（原清单续做时使用的就是这一份）。"""
    try:
        return _engine().manifest_for_plan(plan_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/backfill-plans/{plan_id}/run")
def run_backfill_plan(
    plan_id: str,
    limit: int | None = Query(default=None, ge=0, description="本次最多处理的未确认日期数"),
    reason: str = Query(default="启动回填"),
    continue_on_unreadable: bool = Query(
        default=False, description="遇到不可读来源时是否跳过继续后面的日期"),
) -> dict[str, Any]:
    """执行或续做回填计划；已确认日期不会重复写入。"""
    try:
        return _engine().run_plan(
            plan_id,
            limit=limit,
            reason=reason,
            continue_on_unreadable=continue_on_unreadable,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
