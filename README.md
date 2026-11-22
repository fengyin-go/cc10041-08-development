# 流批一体数据处理中心

统一管理流式加工和批量计算的数据处理平台，覆盖数据清洗、转换、关联和结果落地

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8016/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5196/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8016`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 流式数据源 | `stream_source` | 流式源 | 源名称、消息队列主题、序列化格式 |
| 转换规则 | `transform_rule` | 转换规则 | 规则名称、输入Schema、输出Schema |
| 批量计算管道 | `batch_pipeline` | 计算管道 | 管道名称、输入源、输出目标 |
| 数据关联合并 | `join_config` | 关联配置 | 关联名称、左表源、右表源 |
| 检查点管理 | `checkpoint_mgr` | 检查点 | 管道名称、检查点序列、创建时间 |
| 异常处理策略 | `error_handler` | 异常策略 | 策略名称、适用管道、异常类型 |
| 结果写入器 | `result_writer` | 写入配置 | 配置名称、写入目标类型、输出格式 |
| 迟到数据处理 | `late_data` | 迟到策略 | 策略名称、最大延迟时间、迟到处理方式 |
| 窗口配置 | `window_config` | 窗口配置 | 窗口名称、窗口类型、窗口长度 |
| 聚合计算规则 | `aggregation_rule` | 聚合规则 | 规则名称、聚合函数、分组键 |
| 数据清洗链 | `data_cleanse` | 清洗步骤 | 步骤序号、清洗类型、输入字段 |
| 管道监控 | `pipeline_monitor` | 监控指标 | 指标名称、监控目标、告警阈值 |
| 历史回填 | `backfill_handler` | 回填任务 | 任务名称、目标管道、回填时间范围 |
| 输出审计 | `output_audit` | 审计记录 | 管道名称、输出目标、输出时间 |
| Schema注册 | `schema_registry` | Schema定义 | Schema名称、字段列表、版本号 |
| 数据分区 | `data_partition` | 分区配置 | 分区名称、分区键、分区类型 |
| 管道编排DAG | `pipeline_dag` | 编排图 | 图名称、节点列表、依赖关系 |
| Iceberg表管理 | `iceberg_table` | Iceberg表 | 表名、所属业务域、存储格式 |
| 数据采样 | `data_sample` | 采样配置 | 配置名称、采样比例、采样方式 |
| 计算血缘追踪 | `lineage_tracker` | 血缘条目 | 输入表、转换管道、输出表 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 分区覆盖清单与历史回填

数据分区除了登记和启停，还提供「按回填日期范围查询分区覆盖清单」的入口。
同一个日期可能同时命中日分区和包含它的月归档，两份都可能是已核准版本；
清单按「日分区优先」为每一天确定唯一读取来源，并把未覆盖日期单独列出。

```bash
# 查 4 日到 9 日的订单覆盖清单（8、9 日未覆盖）
curl "http://127.0.0.1:8016/api/partitions/coverage?dataset=订单&start=2026-11-04&end=2026-11-09"

# 用覆盖清单准备回填计划（相同范围重复提交会复用原计划）
curl -X POST "http://127.0.0.1:8016/api/partitions/backfill-plans?dataset=订单&start=2026-11-04&end=2026-11-07"

# 执行 / 续做，limit=1 表示本次只处理一个未确认日期
curl -X POST "http://127.0.0.1:8016/api/partitions/backfill-plans/<plan_id>/run?limit=1"

# 核对：已确认、尚待处理、实际不可读的来源和逐日实际版本
curl "http://127.0.0.1:8016/api/partitions/backfill-plans/<plan_id>"
```

计划在创建时冻结每天的来源分区和版本，续做只认这份清单：已确认日期不会重复
写入，来源被停用时记为「不可读」而不是回退到另一份数据，恢复后再续做即可补完。
入口文件：`backend/app/engine/partition_coverage.py`、`backend/app/routers/partition.py`，
前端在「数据分区」页（`frontend/src/views/DataPartitionView.vue`）。

「数据分区」页在原有登记/启停表格下新增了覆盖清单面板：输入数据集和日期范围
即可看到每天的候选已核准来源、本次计划读取的版本和金额、未覆盖日期；
「准备回填计划」把清单冻结成计划，「先跑一天 / 执行 / 续做」按计划逐日写入。

领域：数据工程
子方向：数仓与数据湖
