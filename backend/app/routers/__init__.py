"""业务路由汇总：固定前缀的管道/分区路由放在通用资源路由之前，避免被 /api/{module}/{id} 抢先匹配。"""
from __future__ import annotations

from app.routers import resource, pipeline, partition

ROUTERS = [partition, pipeline, resource]
