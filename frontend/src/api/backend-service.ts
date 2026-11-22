import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  EntryRow,
  LineageCompareResult,
  LineageQueryResult,
  LineageVersionsResult,
  OverviewResult,
  PageResult,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

type VersionedQueryInput = {
  asOf: string
  start: string
  end: string
  mode?: 'report' | 'remediation' | 'both'
  maxDepth?: number
  syncCompare?: boolean
}

async function postJson<T>(path: string, body: unknown, fallback: string): Promise<T> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  if (!response.ok) {
    let detail = fallback
    try {
      const payload = (await response.json()) as { detail?: string }
      if (payload.detail) detail = payload.detail
    } catch {
      // 响应体不是 JSON 时沿用兜底文案
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

export async function queryVersionedLineage(input: VersionedQueryInput): Promise<LineageQueryResult> {
  return postJson<LineageQueryResult>(
    '/api/data_lineage/query',
    {
      as_of: input.asOf,
      start: input.start,
      end: input.end,
      mode: input.mode ?? 'both',
      max_depth: input.maxDepth ?? 6,
      sync_compare: input.syncCompare ?? false,
    },
    '版本定界血缘查询失败',
  )
}

export async function compareVersionedLineage(input: VersionedQueryInput): Promise<LineageCompareResult> {
  return postJson<LineageCompareResult>(
    '/api/data_lineage/compare',
    {
      as_of: input.asOf,
      start: input.start,
      end: input.end,
      max_depth: input.maxDepth ?? 6,
    },
    '版本定界血缘比对失败',
  )
}

export async function backfillLineageEdge(values: Record<string, string>): Promise<ActionResult> {
  return postJson<ActionResult>('/api/data_lineage/backfill', { values }, '补录血缘链路失败')
}

export async function locateLineageVersions(recordId: number): Promise<LineageVersionsResult> {
  const response = await request(`/api/data_lineage/versions/${recordId}`)
  if (!response.ok) {
    if (response.status === 404) throw new Error(`血缘记录 ${recordId} 不存在或已归档`)
    throw new Error('血缘版本定位失败')
  }
  return (await response.json()) as LineageVersionsResult
}

export async function listLineageCompareTasks(): Promise<{ items: EntryRow[]; total: number }> {
  const response = await request('/api/data_lineage/compare/tasks')
  if (!response.ok) throw new Error('血缘比对待办读取失败')
  return await response.json()
}
