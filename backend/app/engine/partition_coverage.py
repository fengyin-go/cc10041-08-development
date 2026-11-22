"""分区覆盖清单与历史回填执行器。

真实数据流：
  data_partition(日分区/月归档) → 分区覆盖清单 → backfill_handler(计划)
  → 逐日读取唯一来源 → 写入结果 → 回填核对

一个回填日期范围可能同时命中「日分区」和包含它的「月归档」：
两边都可能是已核准版本，但历史回填每天只能读取一份数据。本模块在生成
清单时按「日分区优先」冻结每天要读的分区和版本，后续续做只认这份清单，
不会因为归档启停或金额变化把两个来源叠加进合计。
"""
from __future__ import annotations

import copy
import time
from datetime import date, datetime, timedelta
from typing import Any

from app.store import store

PARTITION_KEY = "data_partition"
BACKFILL_KEY = "backfill_handler"

DAILY = "日分区"
MONTHLY = "月归档"
APPROVED = "已核准"

# 计划清单常驻内存，跨请求保留；测试可以通过 reset_plans() 清理。
_PLANS: dict[str, dict[str, Any]] = {}
_PLAN_SEQ = 0


def reset_plans() -> None:
    """清空回填计划状态（测试与本地重置使用）。"""
    global _PLAN_SEQ
    _PLANS.clear()
    _PLAN_SEQ = 0


def _parse_day(value: str, field: str) -> date:
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValueError(f"{field} 必须是 YYYY-MM-DD 格式：{value}")


def _iter_days(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _day_amount(partition: dict[str, Any], day: str) -> float | None:
    """取某个分区在某一天的金额。

    月归档用「日明细」保留结月时每天的金额；日分区只有单一金额。
    月归档没有日明细时，只有正好覆盖一天才认为金额明确。
    """
    source_type = str(partition.get("来源类型") or "")
    if source_type == MONTHLY:
        detail = partition.get("日明细") or {}
        if isinstance(detail, dict):
            value = detail.get(day)
            if value is None:
                return None
            try:
                return round(float(value), 2)
            except (TypeError, ValueError):
                return None
        start = str(partition.get("覆盖开始") or "")
        end = str(partition.get("覆盖结束") or "")
        if start == day and end == day:
            try:
                return round(float(partition.get("金额") or 0), 2)
            except (TypeError, ValueError):
                return None
        return None
    try:
        return round(float(partition.get("金额") or 0), 2)
    except (TypeError, ValueError):
        return None


def _partition_covers(partition: dict[str, Any], day: str) -> bool:
    start = str(partition.get("覆盖开始") or "")
    end = str(partition.get("覆盖结束") or "")
    if start and end:
        return start <= day <= end
    return str(partition.get("日期") or "") == day


def _candidate(partition: dict[str, Any], day: str) -> dict[str, Any] | None:
    if not _partition_covers(partition, day):
        return None
    source_type = str(partition.get("来源类型") or "")
    if source_type not in (DAILY, MONTHLY):
        return None
    amount = _day_amount(partition, day)
    # 月归档声明了日明细时，只有明细里存在的日期才算被这份归档覆盖。
    if source_type == MONTHLY and isinstance(partition.get("日明细"), dict):
        if day not in partition["日明细"]:
            return None
    approved = str(partition.get("核准状态") or "") == APPROVED
    status = str(partition.get("status") or "")
    return {
        "partition_id": int(partition.get("id", 0)),
        "partition_name": str(partition.get("分区名称") or ""),
        "source_type": source_type,
        "version": str(partition.get("版本") or ""),
        "amount": amount,
        "approved": approved,
        "status": status,
        "readable": approved and status == "已启用",
    }


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[int, int]:
    # 日分区优先；同类型时版本号大的优先，保证选择稳定。
    type_rank = 0 if candidate["source_type"] == DAILY else 1
    version = str(candidate.get("version") or "")
    return (type_rank, -int("".join(c for c in version if c.isdigit()) or "0"))


class PartitionCoverageEngine:
    """按回填日期范围生成覆盖清单，并驱动回填计划逐日执行。"""

    partition_key: str = PARTITION_KEY
    backfill_key: str = BACKFILL_KEY

    # ------------------------------------------------------------------
    # 查询：覆盖清单
    # ------------------------------------------------------------------
    def preview(
        self,
        *,
        dataset: str,
        start: str,
        end: str,
        policy: str = "daily_first",
    ) -> dict[str, Any]:
        """按日期范围列出每天的已核准来源、本次计划读取的版本和未覆盖日期。"""
        start_day = _parse_day(start, "开始日期")
        end_day = _parse_day(end, "结束日期")
        if start_day > end_day:
            raise ValueError("开始日期不能晚于结束日期")
        dataset = str(dataset or "").strip()
        if not dataset:
            raise ValueError("数据集不能为空")

        partitions = [
            row for row in store.rows(self.partition_key)
            if str(row.get("数据集") or "") == dataset
            and str(row.get("核准状态") or "") == APPROVED
        ]

        days: list[dict[str, Any]] = []
        uncovered: list[str] = []
        overlap: list[str] = []
        selected_total = 0.0

        for current in _iter_days(start_day, end_day):
            day = current.isoformat()
            candidates = [
                candidate for row in partitions
                if (candidate := _candidate(row, day)) is not None
            ]
            candidates.sort(key=_candidate_sort_key)

            selected = next((c for c in candidates if c["readable"]), None)
            if not candidates:
                uncovered.append(day)
                status = "未覆盖"
            elif selected is None:
                status = "来源不可读"
            else:
                status = "待处理"
                selected_total += float(selected["amount"] or 0)

            if len(candidates) > 1:
                overlap.append(day)

            days.append({
                "date": day,
                "candidates": candidates,
                "selected": selected,
                "selected_partition_id": selected["partition_id"] if selected else None,
                "selected_partition_name": selected["partition_name"] if selected else None,
                "selected_source_type": selected["source_type"] if selected else None,
                "selected_version": selected["version"] if selected else None,
                "selected_amount": selected["amount"] if selected else None,
                "covered": bool(candidates),
                "status": status,
            })

        return {
            "dataset": dataset,
            "start": start_day.isoformat(),
            "end": end_day.isoformat(),
            "policy": policy,
            "day_count": len(days),
            "dates": days,
            "uncovered_dates": uncovered,
            "overlap_dates": overlap,
            "selected_count": sum(1 for d in days if d["selected"]),
            "planned_total_amount": round(selected_total, 2),
            "generated_at": int(time.time() * 1000),
        }

    # ------------------------------------------------------------------
    # 计划：冻结本次要读的范围和版本
    # ------------------------------------------------------------------
    def create_plan(
        self,
        *,
        dataset: str,
        start: str,
        end: str,
        reuse: bool = True,
        policy: str = "daily_first",
        create_task: bool = True,
    ) -> tuple[dict[str, Any], bool]:
        """创建（或复用）回填计划，返回 (计划报告, 是否新建)。

        同一数据集 + 同一日期范围默认复用已有计划，避免重复提交时重新
        扫描来源、叠加金额；确实要重开时可传入 reuse=False。
        """
        global _PLAN_SEQ
        if reuse:
            for plan in _PLANS.values():
                if (
                    plan["dataset"] == str(dataset).strip()
                    and plan["start"] == start
                    and plan["end"] == end
                    and plan["policy"] == policy
                ):
                    return self.report(plan["plan_id"]), False
        manifest = self.preview(dataset=dataset, start=start, end=end, policy=policy)
        _PLAN_SEQ += 1
        plan_id = (
            f"bf-{manifest['start'].replace('-', '')}-"
            f"{manifest['end'].replace('-', '')}-{_PLAN_SEQ:03d}"
        )
        items = []
        for day in manifest["dates"]:
            item = copy.deepcopy(day)
            item.update({
                "status": "待处理" if day["selected"] else day["status"],
                "write_count": 0,
                "written_amount": None,
                "actual_partition_id": None,
                "actual_partition_name": None,
                "actual_source_type": None,
                "actual_version": None,
                "actual_amount": None,
                "confirmed_at": None,
                "last_error": None if day["selected"] else (
                    None if day["covered"] else "该日期没有任何已核准来源"
                ),
                "attempts": [],
            })
            items.append(item)

        plan: dict[str, Any] = {
            "plan_id": plan_id,
            "dataset": manifest["dataset"],
            "start": manifest["start"],
            "end": manifest["end"],
            "policy": policy,
            "status": "待执行",
            "created_at": int(time.time() * 1000),
            "items": items,
            "uncovered_dates": manifest["uncovered_dates"],
            "overlap_dates": manifest["overlap_dates"],
            "planned_total_amount": manifest["planned_total_amount"],
            "selected_count": manifest["selected_count"],
            "write_log": [],
            "runs": [],
            "task_id": None,
        }
        _PLANS[plan_id] = plan
        if create_task:
            plan["task_id"] = self._create_task(plan)
        return self.report(plan_id), True

    def list_plans(self) -> list[dict[str, Any]]:
        return [self.report(plan_id) for plan_id in sorted(_PLANS)]

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        return _PLANS.get(plan_id)

    # ------------------------------------------------------------------
    # 执行：逐日读取唯一来源
    # ------------------------------------------------------------------
    def run_plan(
        self,
        plan_id: str,
        *,
        limit: int | None = None,
        reason: str = "启动回填",
        continue_on_unreadable: bool = False,
    ) -> dict[str, Any]:
        """执行或续做回填计划。

        - 已确认的日期直接跳过，不重复写入；
        - 只读取计划里冻结选中的那个来源和版本，不做来源回退或叠加；
        - 来源当前不可读时记为「不可读」并默认停在该日期，后面的日期保持
          「待处理」，等来源恢复后再次续做；确需跳过时可传
          continue_on_unreadable=True；
        - limit 控制本次最多处理多少个尚未确认的日期，便于分段执行。
        """
        plan = _PLANS.get(plan_id)
        if plan is None:
            raise KeyError(f"回填计划 {plan_id} 不存在")

        if limit is not None and limit < 0:
            raise ValueError("limit 不能为负数")

        run_no = len(plan["runs"]) + 1
        run_record: dict[str, Any] = {
            "run_no": run_no,
            "run_id": f"{plan_id}-r{run_no}",
            "reason": reason,
            "started_at": int(time.time() * 1000),
            "limit": limit,
            "written": [],
            "blocked": [],
            "skipped_confirmed": [],
            "processed": 0,
        }

        already_complete = all(item["status"] == "已确认" for item in plan["items"])
        if already_complete:
            run_record["finished_at"] = int(time.time() * 1000)
            run_record["already_complete"] = True
            plan["runs"].append(run_record)
            self._sync_task(plan)
            report = self.report(plan_id)
            report["already_complete"] = True
            return report

        newly_processed = 0
        for item in plan["items"]:
            if item["status"] == "已确认":
                run_record["skipped_confirmed"].append(item["date"])
                continue
            if item["status"] == "未覆盖":
                continue
            if limit is not None and newly_processed >= limit:
                break

            partition = store.find(self.partition_key, int(item["selected_partition_id"]))
            readable = bool(
                partition
                and str(partition.get("核准状态") or "") == APPROVED
                and str(partition.get("status") or "") == "已启用"
            )
            if not readable:
                current_status = str(partition.get("status") or "已删除") if partition else "已删除"
                item["status"] = "不可读"
                item["last_error"] = (
                    f"计划读取来源「{item['selected_partition_name']}」"
                    f"（{item['selected_version']}）当前状态为{current_status}，不可读"
                )
                item["attempts"].append({
                    "run_id": run_record["run_id"],
                    "at": int(time.time() * 1000),
                    "result": "不可读",
                    "partition_status": current_status,
                })
                run_record["blocked"].append({
                    "date": item["date"],
                    "partition_id": item["selected_partition_id"],
                    "partition_name": item["selected_partition_name"],
                    "source_type": item["selected_source_type"],
                    "version": item["selected_version"],
                    "partition_status": current_status,
                    "reason": item["last_error"],
                })
                if not continue_on_unreadable:
                    run_record["stopped_at"] = item["date"]
                    break
                newly_processed += 1
                continue

            amount = item["selected_amount"]
            write_entry = {
                "seq": len(plan["write_log"]) + 1,
                "date": item["date"],
                "partition_id": item["selected_partition_id"],
                "partition_name": item["selected_partition_name"],
                "source_type": item["selected_source_type"],
                "version": item["selected_version"],
                "amount": amount,
                "run_id": run_record["run_id"],
                "written_at": int(time.time() * 1000),
            }
            plan["write_log"].append(write_entry)
            item["status"] = "已确认"
            item["write_count"] += 1
            item["written_amount"] = amount
            item["actual_partition_id"] = write_entry["partition_id"]
            item["actual_partition_name"] = write_entry["partition_name"]
            item["actual_source_type"] = write_entry["source_type"]
            item["actual_version"] = write_entry["version"]
            item["actual_amount"] = amount
            item["confirmed_at"] = write_entry["written_at"]
            item["last_error"] = None
            item["attempts"].append({
                "run_id": run_record["run_id"],
                "at": write_entry["written_at"],
                "result": "已写入",
                "amount": amount,
            })
            run_record["written"].append(write_entry)
            run_record["processed"] += 1
            newly_processed += 1

        run_record["finished_at"] = int(time.time() * 1000)
        plan["runs"].append(run_record)

        plan["status"] = self._plan_status(plan)
        self._sync_task(plan)
        return self.report(plan_id)

    def _plan_status(self, plan: dict[str, Any]) -> str:
        statuses = [item["status"] for item in plan["items"]]
        if all(status == "已确认" for status in statuses):
            return "已完成"
        has_pending = any(status == "待处理" for status in statuses)
        has_unreadable = any(status == "不可读" for status in statuses)
        if has_unreadable:
            return "已阻塞" if has_pending else "部分失败"
        if has_pending:
            return "回填中"
        return "部分失败"

    # ------------------------------------------------------------------
    # 核对：已确认 / 尚待处理 / 实际不可读
    # ------------------------------------------------------------------
    def report(self, plan_id: str) -> dict[str, Any]:
        plan = _PLANS.get(plan_id)
        if plan is None:
            raise KeyError(f"回填计划 {plan_id} 不存在")

        per_day = []
        for item in plan["items"]:
            partition = (
                store.find(self.partition_key, int(item["selected_partition_id"]))
                if item["selected_partition_id"] is not None else None
            )
            current_status = (
                str(partition.get("status") or "") if partition else "已删除"
            )
            per_day.append({
                "date": item["date"],
                "planned_partition_id": item["selected_partition_id"],
                "planned_partition_name": item["selected_partition_name"],
                "planned_source_type": item["selected_source_type"],
                "planned_version": item["selected_version"],
                "planned_amount": item["selected_amount"],
                "actual_partition_id": item["actual_partition_id"],
                "actual_partition_name": item["actual_partition_name"],
                "actual_source_type": item["actual_source_type"],
                "actual_version": item["actual_version"],
                "actual_amount": item["actual_amount"],
                "write_count": item["write_count"],
                "status": item["status"],
                "current_partition_status": current_status,
                "last_error": item["last_error"],
            })

        confirmed = [item["date"] for item in plan["items"] if item["status"] == "已确认"]
        pending = [item["date"] for item in plan["items"] if item["status"] == "待处理"]
        unreadable = [
            {
                "date": item["date"],
                "partition_id": item["selected_partition_id"],
                "partition_name": item["selected_partition_name"],
                "source_type": item["selected_source_type"],
                "version": item["selected_version"],
                "planned_amount": item["selected_amount"],
                "current_partition_status": next(
                    (d["current_partition_status"] for d in per_day if d["date"] == item["date"]),
                    "",
                ),
                "reason": item["last_error"],
            }
            for item in plan["items"] if item["status"] == "不可读"
        ]
        uncovered = [item["date"] for item in plan["items"] if item["status"] == "未覆盖"]

        total_written_amount = round(
            sum(float(entry.get("amount") or 0) for entry in plan["write_log"]), 2)
        return {
            "plan_id": plan["plan_id"],
            "dataset": plan["dataset"],
            "start": plan["start"],
            "end": plan["end"],
            "policy": plan["policy"],
            "status": plan["status"],
            "task_id": plan["task_id"],
            "created_at": plan["created_at"],
            "planned_total_amount": plan["planned_total_amount"],
            "total_written_amount": total_written_amount,
            "total_write_count": len(plan["write_log"]),
            "counts": {
                "total": len(plan["items"]),
                "confirmed": len(confirmed),
                "pending": len(pending),
                "unreadable": len(unreadable),
                "uncovered": len(uncovered),
            },
            "confirmed_dates": confirmed,
            "pending_dates": pending,
            "unreadable": unreadable,
            "uncovered_dates": uncovered,
            "overlap_dates": plan["overlap_dates"],
            "per_day": per_day,
            "write_log": copy.deepcopy(plan["write_log"]),
            "runs": copy.deepcopy(plan["runs"]),
        }

    def manifest_for_plan(self, plan_id: str) -> dict[str, Any]:
        """用计划冻结的内容还原一份覆盖清单，供前端和调度核对。"""
        plan = _PLANS.get(plan_id)
        if plan is None:
            raise KeyError(f"回填计划 {plan_id} 不存在")
        days = []
        for item in copy.deepcopy(plan["items"]):
            days.append({
                key: item.get(key) for key in (
                    "date", "candidates", "selected", "selected_partition_id",
                    "selected_partition_name", "selected_source_type",
                    "selected_version", "selected_amount", "covered", "status",
                )
            })
        return {
            "plan_id": plan_id,
            "dataset": plan["dataset"],
            "start": plan["start"],
            "end": plan["end"],
            "policy": plan["policy"],
            "day_count": len(days),
            "dates": days,
            "uncovered_dates": list(plan["uncovered_dates"]),
            "overlap_dates": list(plan["overlap_dates"]),
            "selected_count": plan["selected_count"],
            "planned_total_amount": plan["planned_total_amount"],
        }

    # ------------------------------------------------------------------
    # 回填任务联动
    # ------------------------------------------------------------------
    def _create_task(self, plan: dict[str, Any]) -> int:
        tasks = store.rows(self.backfill_key)
        next_id = max((int(row.get("id", 0)) for row in tasks), default=0) + 1
        tasks.append({
            "id": next_id,
            "status": "待调度",
            "pending": True,
            "abnormal": False,
            "任务名称": f"{plan['dataset']}回填 {plan['start']}~{plan['end']}",
            "目标管道": f"{plan['dataset']}回填管道",
            "回填时间范围": f"{plan['start']}~{plan['end']}",
            "处理进度": f"已确认 0/{len(plan['items'])} 天",
            "执行状态": f"计划 {plan['plan_id']} 待运行",
            "plan_id": plan["plan_id"],
            "数据集": plan["dataset"],
        })
        return next_id

    def _sync_task(self, plan: dict[str, Any]) -> None:
        task_id = plan.get("task_id")
        task = store.find(self.backfill_key, int(task_id)) if task_id else None
        if task is None:
            plan["task_id"] = self._create_task(plan)
            task = store.find(self.backfill_key, int(plan["task_id"]))
        if task is None:
            return
        report = self.report(plan["plan_id"])
        confirmed = report["counts"]["confirmed"]
        total = report["counts"]["total"]
        task["处理进度"] = f"已确认 {confirmed}/{total} 天，写入 {report['total_write_count']} 次"
        task["status"] = {
            "待执行": "待调度",
            "回填中": "回填中",
            "已阻塞": "回填中",
            "已完成": "已完成",
            "部分失败": "回填失败",
        }.get(plan["status"], "回填中")
        task["执行状态"] = (
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} {plan['status']}，"
            f"累计写入 {report['total_written_amount']:.2f} 元"
        )
        task["pending"] = task["status"] in ("待调度", "回填中")
        task["abnormal"] = task["status"] == "回填失败"


_coverage_engine = PartitionCoverageEngine()


def coverage_engine() -> PartitionCoverageEngine:
    return _coverage_engine
