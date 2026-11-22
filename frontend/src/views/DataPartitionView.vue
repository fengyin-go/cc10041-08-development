<template>
  <section class="page partition-board" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}；按回填日期范围先查覆盖清单，再据此准备回填读取。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <section class="coverage-panel" data-testid="coverage-panel">
      <header class="coverage-head">
        <div>
          <h3>回填覆盖清单</h3>
          <p class="page-desc">
            同一日期可能同时命中日分区和包含它的月归档；清单会为每一天确定唯一读取来源。
          </p>
        </div>
      </header>

      <form class="analysis-form" data-testid="coverage-form" @submit.prevent="loadCoverage">
        <label>
          数据集
          <input v-model="coverageForm.dataset" data-testid="coverage-dataset" placeholder="例如 订单" />
        </label>
        <label>
          开始日期
          <input v-model="coverageForm.start" data-testid="coverage-start" type="date" />
        </label>
        <label>
          结束日期
          <input v-model="coverageForm.end" data-testid="coverage-end" type="date" />
        </label>
        <button class="btn" data-testid="coverage-query" type="submit">查询覆盖清单</button>
        <button class="btn primary" data-testid="prepare-plan" type="button" @click="preparePlan">
          准备回填计划
        </button>
      </form>

      <p v-if="coverageError" class="error-text" data-testid="coverage-error">{{ coverageError }}</p>

      <template v-if="manifest">
        <div class="analysis-summary" data-testid="coverage-summary">
          <article class="analysis-card">
            <span class="stat-label">日期数</span>
            <strong class="stat-value">{{ manifest.day_count }}</strong>
          </article>
          <article class="analysis-card">
            <span class="stat-label">已选来源</span>
            <strong class="stat-value">{{ manifest.selected_count }}</strong>
          </article>
          <article class="analysis-card">
            <span class="stat-label">重叠日期</span>
            <strong class="stat-value">{{ manifest.overlap_dates.length }}</strong>
          </article>
          <article class="analysis-card">
            <span class="stat-label">计划合计（元）</span>
            <strong class="stat-value">{{ manifest.planned_total_amount.toFixed(2) }}</strong>
          </article>
        </div>

        <p class="status-legend" data-testid="uncovered-dates">
          <span class="legend-item">
            未覆盖日期：{{ manifest.uncovered_dates.length ? manifest.uncovered_dates.join('、') : '无' }}
          </span>
        </p>

        <table class="data-table" data-testid="coverage-table">
          <thead>
            <tr>
              <th>日期</th>
              <th>候选来源（已核准）</th>
              <th>本次读取版本</th>
              <th>读取来源</th>
              <th>金额（元）</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="day in manifest.dates" :key="day.date" :data-testid="`coverage-${day.date}`">
              <td>{{ day.date }}</td>
              <td>
                <span v-if="!day.candidates.length" class="muted">无</span>
                <span v-for="candidate in day.candidates" :key="candidate.partition_id" class="source-chip">
                  {{ candidate.partition_name }}（{{ candidate.source_type }} · {{ candidate.version }} ·
                  {{ formatAmount(candidate.amount) }} 元）
                </span>
              </td>
              <td>{{ day.selected_version ?? '—' }}</td>
              <td>{{ day.selected_partition_name ?? '—' }}</td>
              <td>{{ formatAmount(day.selected_amount) }}</td>
              <td>{{ day.status }}</td>
            </tr>
          </tbody>
        </table>
      </template>
    </section>

    <section v-if="plan" class="coverage-panel" data-testid="plan-panel">
      <header class="coverage-head">
        <div>
          <h3>回填计划 {{ plan.plan_id }}</h3>
          <p class="page-desc">
            相同范围重复提交会复用这一份计划；续做只按冻结的来源和版本读取，已确认日期不会重复写入。
          </p>
        </div>
        <div class="page-actions">
          <button class="btn primary" data-testid="run-plan" type="button" @click="runPlan()">
            执行 / 续做
          </button>
          <button class="btn" data-testid="run-plan-step" type="button" @click="runPlan(true)">
            先跑一天
          </button>
          <button class="btn ghost" data-testid="refresh-plan" type="button" @click="refreshPlan">
            刷新核对
          </button>
        </div>
      </header>

      <p v-if="planNotice" class="status-legend" data-testid="plan-notice">{{ planNotice }}</p>
      <p v-if="planError" class="error-text" data-testid="plan-error">{{ planError }}</p>

      <div class="analysis-summary">
        <article class="analysis-card">
          <span class="stat-label">计划状态</span>
          <strong class="stat-value" data-testid="plan-status">{{ plan.status }}</strong>
        </article>
        <article class="analysis-card">
          <span class="stat-label">已确认日期</span>
          <strong class="stat-value" data-testid="plan-confirmed">{{ plan.counts.confirmed }}/{{ plan.counts.total }}</strong>
        </article>
        <article class="analysis-card">
          <span class="stat-label">尚待处理</span>
          <strong class="stat-value" data-testid="plan-pending">{{ plan.pending_dates.length }}</strong>
        </article>
        <article class="analysis-card">
          <span class="stat-label">不可读来源</span>
          <strong class="stat-value" data-testid="plan-unreadable">{{ plan.counts.unreadable }}</strong>
        </article>
        <article class="analysis-card">
          <span class="stat-label">累计写入（元 / 次数）</span>
          <strong class="stat-value" data-testid="plan-written">
            {{ plan.total_written_amount.toFixed(2) }} / {{ plan.total_write_count }}
          </strong>
        </article>
      </div>

      <p class="status-legend">
        <span class="legend-item" data-testid="plan-confirmed-dates">
          已确认：{{ plan.confirmed_dates.length ? plan.confirmed_dates.join('、') : '无' }}
        </span>
        <span class="legend-item" data-testid="plan-pending-dates">
          尚待处理：{{ plan.pending_dates.length ? plan.pending_dates.join('、') : '无' }}
        </span>
        <span class="legend-item" data-testid="plan-uncovered-dates">
          未覆盖：{{ plan.uncovered_dates.length ? plan.uncovered_dates.join('、') : '无' }}
        </span>
      </p>

      <table v-if="plan.unreadable.length" class="data-table" data-testid="unreadable-table">
        <thead>
          <tr><th>日期</th><th>计划来源</th><th>来源类型</th><th>版本</th><th>当前状态</th><th>原因</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in plan.unreadable" :key="item.date" :data-testid="`unreadable-${item.date}`">
            <td>{{ item.date }}</td>
            <td>{{ item.partition_name }}</td>
            <td>{{ item.source_type }}</td>
            <td>{{ item.version }}</td>
            <td>{{ item.current_partition_status }}</td>
            <td>{{ item.reason }}</td>
          </tr>
        </tbody>
      </table>

      <table class="data-table" data-testid="plan-table">
        <thead>
          <tr>
            <th>日期</th>
            <th>计划来源</th>
            <th>实际来源</th>
            <th>实际版本</th>
            <th>金额（元）</th>
            <th>写入次数</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="day in plan.per_day" :key="day.date" :data-testid="`plan-${day.date}`">
            <td>{{ day.date }}</td>
            <td>{{ day.planned_partition_name ?? '—' }}（{{ day.planned_version ?? '—' }}）</td>
            <td>{{ day.actual_partition_name ?? '—' }}</td>
            <td>{{ day.actual_version ?? '—' }}</td>
            <td>{{ formatAmount(day.actual_amount) }}</td>
            <td>{{ day.write_count }}</td>
            <td>{{ day.status }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  createEntry,
  downloadEntries,
  listEntries,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import {
  loadBackfillPlan,
  prepareBackfillPlan,
  queryCoverage,
  runBackfillPlan,
} from '@/api/partition'
import type { BackfillPlanReport, CoverageManifest } from '@/api/partition'
import type { EntryRow } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])

// 回填覆盖清单与计划
const coverageForm = ref({ dataset: '订单', start: '2026-11-04', end: '2026-11-07' })
const manifest = ref<CoverageManifest | null>(null)
const plan = ref<BackfillPlanReport | null>(null)
const coverageError = ref('')
const planError = ref('')
const planNotice = ref('')
const busy = ref(false)

function formatAmount(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return value.toFixed(2)
}

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) throw new Error(result.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function loadCoverage() {
  coverageError.value = ''
  const { dataset, start, end } = coverageForm.value
  try {
    manifest.value = await queryCoverage(dataset, start, end)
  } catch (error) {
    manifest.value = null
    coverageError.value = error instanceof Error ? error.message : '覆盖清单读取失败'
  }
}

async function preparePlan() {
  coverageError.value = ''
  planError.value = ''
  planNotice.value = ''
  const { dataset, start, end } = coverageForm.value
  try {
    const report = await prepareBackfillPlan(dataset, start, end)
    plan.value = report
    planNotice.value = report.created ? '已按本次范围创建回填计划' : '相同范围已存在计划，沿用原清单续做'
    await loadCoverage()
    await reload()
  } catch (error) {
    coverageError.value = error instanceof Error ? error.message : '回填计划准备失败'
  }
}

async function runPlan(stepOnce = false) {
  if (!plan.value || busy.value) return
  busy.value = true
  planError.value = ''
  planNotice.value = ''
  try {
    const report = await runBackfillPlan(plan.value.plan_id, stepOnce ? 1 : undefined)
    plan.value = report
    if (report.already_complete) {
      planNotice.value = '计划已全部确认，本次没有重复写入'
    } else if (report.counts.unreadable) {
      planNotice.value =
        `有 ${report.counts.unreadable} 天计划来源不可读，已停在最早受阻日期；` +
        `已确认 ${report.confirmed_dates.join('、') || '无'}，` +
        `尚待处理 ${report.pending_dates.join('、') || '无'}`
    } else {
      planNotice.value = `本次已确认 ${report.counts.confirmed}/${report.counts.total} 天`
    }
    await reload()
  } catch (error) {
    planError.value = error instanceof Error ? error.message : '回填计划执行失败'
  } finally {
    busy.value = false
  }
}

async function refreshPlan() {
  if (!plan.value) return
  planError.value = ''
  try {
    plan.value = await loadBackfillPlan(plan.value.plan_id)
  } catch (error) {
    planError.value = error instanceof Error ? error.message : '回填计划读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

onMounted(async () => {
  await reload()
  await loadCoverage()
})
</script>

<style scoped>
.coverage-panel {
  margin-top: 20px;
  padding: 14px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.coverage-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 8px;
}
.coverage-head h3 { margin: 0 0 4px; font-size: 15px; }
.source-chip {
  display: inline-block;
  margin: 0 8px 4px 0;
  padding: 2px 8px;
  border-radius: 10px;
  background: #eef2ff;
  color: #3730a3;
  font-size: 12px;
}
.muted { color: var(--muted); }
</style>
