/** 分区覆盖清单与历史回填计划的请求封装。 */
import { request } from './client'

export type CoverageCandidate = {
  partition_id: number
  partition_name: string
  source_type: string
  version: string
  amount: number | null
  approved: boolean
  status: string
  readable: boolean
}

export type CoverageDay = {
  date: string
  candidates: CoverageCandidate[]
  selected_partition_id: number | null
  selected_partition_name: string | null
  selected_source_type: string | null
  selected_version: string | null
  selected_amount: number | null
  covered: boolean
  status: string
}

export type CoverageManifest = {
  dataset: string
  start: string
  end: string
  policy: string
  day_count: number
  dates: CoverageDay[]
  uncovered_dates: string[]
  overlap_dates: string[]
  selected_count: number
  planned_total_amount: number
}

export type PlanDay = {
  date: string
  planned_partition_id: number | null
  planned_partition_name: string | null
  planned_source_type: string | null
  planned_version: string | null
  planned_amount: number | null
  actual_partition_id: number | null
  actual_partition_name: string | null
  actual_source_type: string | null
  actual_version: string | null
  actual_amount: number | null
  write_count: number
  status: string
  current_partition_status: string
  last_error: string | null
}

export type UnreadableDay = {
  date: string
  partition_id: number | null
  partition_name: string | null
  source_type: string | null
  version: string | null
  planned_amount: number | null
  current_partition_status: string
  reason: string | null
}

export type BackfillPlanReport = {
  plan_id: string
  dataset: string
  start: string
  end: string
  policy: string
  status: string
  task_id: number | null
  planned_total_amount: number
  total_written_amount: number
  total_write_count: number
  counts: {
    total: number
    confirmed: number
    pending: number
    unreadable: number
    uncovered: number
  }
  confirmed_dates: string[]
  pending_dates: string[]
  unreadable: UnreadableDay[]
  uncovered_dates: string[]
  overlap_dates: string[]
  per_day: PlanDay[]
  write_log: {
    seq: number
    date: string
    partition_id: number
    partition_name: string
    source_type: string
    version: string
    amount: number
    run_id: string
    written_at: number
  }[]
  runs: {
    run_no: number
    run_id: string
    reason: string
    limit: number | null
    written: unknown[]
    blocked: unknown[]
    skipped_confirmed: string[]
    processed: number
    stopped_at?: string
    already_complete?: boolean
  }[]
  created?: boolean
  already_complete?: boolean
}

function query(values: Record<string, string | number | boolean | undefined>): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== '') params.set(key, String(value))
  }
  return params.toString()
}

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error((payload as { detail?: string }).detail || fallback)
  }
  return payload as T
}

export async function queryCoverage(
  dataset: string,
  start: string,
  end: string,
): Promise<CoverageManifest> {
  const response = await request(
    `/api/partitions/coverage?${query({ dataset, start, end })}`,
  )
  return readJson<CoverageManifest>(response, '分区覆盖清单读取失败')
}

export async function prepareBackfillPlan(
  dataset: string,
  start: string,
  end: string,
): Promise<BackfillPlanReport> {
  const response = await request(
    `/api/partitions/backfill-plans?${query({ dataset, start, end })}`,
    { method: 'POST' },
  )
  return readJson<BackfillPlanReport>(response, '回填计划准备失败')
}

export async function runBackfillPlan(
  planId: string,
  limit?: number,
): Promise<BackfillPlanReport> {
  const response = await request(
    `/api/partitions/backfill-plans/${encodeURIComponent(planId)}/run?${query({ limit })}`,
    { method: 'POST' },
  )
  return readJson<BackfillPlanReport>(response, '回填计划执行失败')
}

export async function loadBackfillPlan(planId: string): Promise<BackfillPlanReport> {
  const response = await request(
    `/api/partitions/backfill-plans/${encodeURIComponent(planId)}`,
  )
  return readJson<BackfillPlanReport>(response, '回填计划读取失败')
}
