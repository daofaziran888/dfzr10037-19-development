/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

export type LineageStep = {
  记录ID: number
  链路编号: string
  上游实体: string
  下游实体: string
  转换描述: string
  生效时间: string
  记录时间: string
  版本: number
  状态: string
  迟到补录: boolean
  报告时点后登记: boolean
  排除原因?: string
}

export type LineagePath = {
  path_id: string
  nodes: string[]
  display: string
  hop_count: number
  record_ids: number[]
  chain_codes: string[]
  steps: LineageStep[]
  late_edge_count: number
}

export type LineageModeResult = {
  mode: 'report' | 'remediation'
  label: string
  as_of: string
  start: string
  end: string
  max_depth: number
  path_count: number
  paths: LineagePath[]
  path_ids: string[]
  record_ids: number[]
  edge_count: number
  excluded_edges: LineageStep[]
}

export type LineageQueryResult = {
  query_id: string
  mode: 'report' | 'remediation' | 'both'
  as_of: string
  start: string
  end: string
  max_depth: number
  reused: boolean
  duplicate_requests: number
  modes: Partial<Record<'report' | 'remediation', LineageModeResult>>
  paths: LineagePath[]
  path_ids: string[]
  record_ids: number[]
  path_count: number
  labels: Record<string, string>
  comparison?: LineageCompareResult
}

export type LineageCompareResult = {
  ok: boolean
  task: EntryRow & Record<string, string | number | boolean>
  task_reused: boolean
  as_of: string
  request_date: string
  query_id: string
  base: {
    mode: string
    label: string
    as_of: string
    record_cutoff: string
    knowledge_cutoff: string
    path_count: number
    path_ids: string[]
    record_ids: number[]
    paths: LineagePath[]
  }
  target: {
    mode: string
    label: string
    as_of: string
    record_cutoff: string
    knowledge_cutoff: string
    path_count: number
    path_ids: string[]
    record_ids: number[]
    paths: LineagePath[]
  }
  diff: {
    added_paths: LineagePath[]
    removed_paths: LineagePath[]
    added_edges: LineageStep[]
    removed_edges: LineageStep[]
    added_path_count: number
    removed_path_count: number
    added_edge_count: number
    removed_edge_count: number
    impact_summary: string
  }
  verification: {
    consumed_record_ids: number[]
    report_reproducible_record_ids: number[]
    remediation_record_ids: number[]
    consistent: boolean
    as_of_labels: Record<string, string>
    note: string
  }
}

export type LineageVersion = EntryRow & {
  版本: number
  当前版本: boolean
  已撤销: boolean
  已被取代: boolean
  生效时间: string
  记录时间: string
}

export type LineageVersionsResult = {
  found: boolean
  record_id: number
  chain: string
  lineage_key: number
  current_record_id: number
  versions: LineageVersion[]
  version_count: number
}
