"""数据分区领域服务：登记、启停之外，提供按回填日期范围查询分区覆盖清单的能力。"""
from __future__ import annotations

from typing import Any

from app.engine.partition_coverage import coverage_engine
from app.services.base import BaseModuleService


class DataPartitionService(BaseModuleService):
    MODULE = "data_partition"
    ENTITY = "分区配置"
    OPERATION_TERM = "存储"
    LIST_FIELDS = ["分区名称", "分区键", "分区类型", "分区数量", "生命周期"]
    REQUIRED_FIELDS = ["分区名称", "分区键", "分区类型"]
    STATUS_ORDER = ["待启用", "已启用", "已停用"]
    ACTION_RULES = {"启用分区": "已启用", "停用分区": "已停用"}
    NEGATIVE_ACTIONS = ["停用分区"]

    def coverage(
        self,
        *,
        dataset: str,
        start: str,
        end: str,
        policy: str = "daily_first",
    ) -> dict[str, Any]:
        """按回填日期范围生成分区覆盖清单，调度可直接用它准备要读的数据。"""
        return coverage_engine().preview(
            dataset=dataset, start=start, end=end, policy=policy)
