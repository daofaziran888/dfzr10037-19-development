<template>
  <section class="page" data-testid="lineage-versioned">
    <header class="page-head">
      <div>
        <h2>数据血缘 · 版本定界查询</h2>
        <p class="page-desc">
          按报告取数时点回溯转换路径。报告复现只看当时已登记的关系，整改核对按如今已知的历史事实取边，
          两种用途各自成图，不混拼路径。
        </p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/data_lineage" data-testid="back-to-lineage">返回血缘链路清单</RouterLink>
      </div>
    </header>

    <form class="analysis-form" data-testid="lineage-query-form" @submit.prevent="runQuery">
      <label>
        <span>报告取数时点</span>
        <input v-model="form.asOf" data-testid="query-as-of" type="date" required />
      </label>
      <label>
        <span>起始实体</span>
        <input v-model="form.start" data-testid="query-start" placeholder="如 ODS订单" required />
      </label>
      <label>
        <span>目标实体</span>
        <input v-model="form.end" data-testid="query-end" placeholder="如 ADS销售报表" required />
      </label>
      <label>
        <span>查询用途</span>
        <select v-model="form.mode" data-testid="query-mode">
          <option value="both">两种用途对照</option>
          <option value="report">报告复现（当时可见）</option>
          <option value="remediation">整改核对（如今已知）</option>
        </select>
      </label>
      <label>
        <span>最大跳数</span>
        <input v-model.number="form.maxDepth" data-testid="query-depth" type="number" min="1" max="12" />
      </label>
      <button class="btn primary" data-testid="run-query" type="submit" :disabled="loading">
        {{ loading ? '查询中…' : '按版本定界查询' }}
      </button>
      <button class="btn" data-testid="sync-compare" type="button" :disabled="busy" @click="syncCompare">
        同步到图谱比对待办
      </button>
    </form>

    <p v-if="errorMessage" class="error-text" data-testid="query-error">{{ errorMessage }}</p>
    <p v-if="notice" class="notice-text" data-testid="query-notice">{{ notice }}</p>

    <template v-if="result">
      <div class="analysis-summary" data-testid="lineage-query-summary">
        <article class="analysis-card">
          <span class="stat-label">查询号</span>
          <strong class="stat-value">{{ result.query_id }}</strong>
        </article>
        <article class="analysis-card">
          <span class="stat-label">报告取数时点</span>
          <strong class="stat-value">{{ result.as_of }}</strong>
        </article>
        <article class="analysis-card">
          <span class="stat-label">重复请求</span>
          <strong class="stat-value" data-testid="duplicate-count">{{ result.duplicate_requests }}</strong>
          <span class="stat-label">{{ result.reused ? '同一时点复用既有结果，未重复返回路径' : '首次查询' }}</span>
        </article>
      </div>

      <div class="mode-grid">
        <section
          v-for="panel in panels"
          :key="panel.mode"
          class="mode-card"
          :data-testid="`mode-${panel.mode}`"
        >
          <header class="mode-head">
            <h3>{{ panel.label }}</h3>
            <span class="mode-count" :data-testid="`mode-count-${panel.mode}`">
              {{ panel.data.path_count }} 条路径 · 消费 {{ panel.data.record_ids.length }} 条关系
            </span>
          </header>

          <ul v-if="panel.data.paths.length" class="path-list">
            <li
              v-for="path in panel.data.paths"
              :key="path.path_id"
              class="path-item"
              :data-testid="`path-${panel.mode}-${path.path_id}`"
            >
              <div class="path-head">
                <strong>{{ path.display }}</strong>
                <span class="path-meta">{{ path.hop_count }} 跳 · {{ path.path_id }}</span>
              </div>
              <table class="mini-table">
                <thead>
                  <tr><th>链路编号</th><th>转换</th><th>生效时间</th><th>记录时间</th><th>标记</th></tr>
                </thead>
                <tbody>
                  <tr v-for="step in path.steps" :key="step.记录ID">
                    <td>{{ step.链路编号 }}<span class="muted"> #{{ step.记录ID }}</span></td>
                    <td>{{ step.上游实体 }} → {{ step.下游实体 }}</td>
                    <td>{{ step.生效时间 }}</td>
                    <td>{{ step.记录时间 }}</td>
                    <td>
                      <span v-if="step.迟到补录" class="tag tag-warn">迟到补录</span>
                      <span v-if="step.报告时点后登记" class="tag tag-warn">时点后登记</span>
                      <span v-if="!step.迟到补录 && !step.报告时点后登记" class="tag">常规</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </li>
          </ul>
          <p v-else class="empty-state">该用途在此时点下没有可用路径。</p>

          <div v-if="panel.data.excluded_edges.length" class="excluded-block" :data-testid="`excluded-${panel.mode}`">
            <h4>被本用途排除的链路</h4>
            <ul>
              <li v-for="edge in panel.data.excluded_edges" :key="edge.记录ID">
                {{ edge.链路编号 }} #{{ edge.记录ID }} {{ edge.上游实体 }} → {{ edge.下游实体 }}
                <span class="muted">（生效 {{ edge.生效时间 }} / 登记 {{ edge.记录时间 }}）</span>
                <em>{{ edge.排除原因 }}</em>
              </li>
            </ul>
          </div>
        </section>
      </div>

      <section v-if="comparison" class="compare-block" data-testid="compare-result">
        <header class="mode-head">
          <h3>图谱比对：版本定界影响差异</h3>
          <span class="mode-count">
            比对任务 {{ comparison.task.任务编号 }} · 对照请求日期 {{ comparison.task.对照请求日期 }} ·
            比对时间 {{ comparison.task.比对时间 }}
          </span>
        </header>
        <p class="compare-summary" data-testid="compare-summary">{{ comparison.diff.impact_summary }}</p>
        <p class="muted">
          基准：{{ comparison.verification.as_of_labels.base }} ·
          对比：{{ comparison.verification.as_of_labels.target }} ·
          {{ comparison.verification.note }}
        </p>
        <p class="muted" data-testid="compare-cutoffs">
          基准取数时点 {{ comparison.base.record_cutoff }}（按报告口径截断）·
          对比取数时点 {{ comparison.target.record_cutoff }}（按如今已知事实）
        </p>
        <div class="diff-grid">
          <div>
            <h4>新增路径（{{ comparison.diff.added_path_count }}）</h4>
            <ul data-testid="diff-added-paths">
              <li v-for="path in comparison.diff.added_paths" :key="path.path_id">{{ path.display }}</li>
              <li v-if="!comparison.diff.added_paths.length" class="muted">无</li>
            </ul>
          </div>
          <div>
            <h4>消失路径（{{ comparison.diff.removed_path_count }}）</h4>
            <ul data-testid="diff-removed-paths">
              <li v-for="path in comparison.diff.removed_paths" :key="path.path_id">{{ path.display }}</li>
              <li v-if="!comparison.diff.removed_paths.length" class="muted">无</li>
            </ul>
          </div>
        </div>
        <table class="mini-table" data-testid="diff-edges">
          <thead>
            <tr><th>差异类型</th><th>记录</th><th>链路编号</th><th>上游</th><th>下游</th><th>生效时间</th><th>记录时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="edge in comparison.diff.added_edges" :key="`add-${edge.记录ID}`">
              <td><span class="tag tag-add">新增</span></td>
              <td>#{{ edge.记录ID }}</td>
              <td>{{ edge.链路编号 }}</td>
              <td>{{ edge.上游实体 }}</td>
              <td>{{ edge.下游实体 }}</td>
              <td>{{ edge.生效时间 }}</td>
              <td>{{ edge.记录时间 }}</td>
            </tr>
            <tr v-for="edge in comparison.diff.removed_edges" :key="`del-${edge.记录ID}`">
              <td><span class="tag tag-del">消失</span></td>
              <td>#{{ edge.记录ID }}</td>
              <td>{{ edge.链路编号 }}</td>
              <td>{{ edge.上游实体 }}</td>
              <td>{{ edge.下游实体 }}</td>
              <td>{{ edge.生效时间 }}</td>
              <td>{{ edge.记录时间 }}</td>
            </tr>
          </tbody>
        </table>
        <p class="muted" data-testid="compare-verification">
          比对基准消费记录：{{ comparison.verification.consumed_record_ids.join(', ') || '—' }}；
          原报告可复现记录：{{ comparison.verification.report_reproducible_record_ids.join(', ') || '—' }}；
          一致性：{{ comparison.verification.consistent ? '一致' : '不一致' }}
        </p>
      </section>
    </template>

    <section class="backfill-block" data-testid="lineage-backfill">
      <header class="mode-head">
        <h3>补录迟到链路</h3>
        <span class="mode-count">声明生效日可以早于今日，登记时间默认今天</span>
      </header>
      <form class="analysis-form" @submit.prevent="submitBackfill">
        <label>
          <span>上游实体</span>
          <input v-model="backfill.上游实体" data-testid="backfill-upstream" required />
        </label>
        <label>
          <span>下游实体</span>
          <input v-model="backfill.下游实体" data-testid="backfill-downstream" required />
        </label>
        <label>
          <span>转换描述</span>
          <input v-model="backfill.转换描述" data-testid="backfill-desc" placeholder="迟到补录的转换" />
        </label>
        <label>
          <span>生效时间</span>
          <input v-model="backfill.生效时间" data-testid="backfill-effective" type="date" required />
        </label>
        <label>
          <span>关联字段</span>
          <input v-model="backfill.关联字段" data-testid="backfill-fields" placeholder="order_id" />
        </label>
        <button class="btn primary" data-testid="submit-backfill" type="submit" :disabled="busy">补录链路</button>
      </form>
      <p v-if="backfillNotice" class="notice-text" data-testid="backfill-notice">{{ backfillNotice }}</p>
    </section>

    <section class="locate-block" data-testid="lineage-locate">
      <header class="mode-head">
        <h3>按原记录定位版本</h3>
        <span class="mode-count">被撤销或重登记的关系仍可用原记录 ID 找回全部版本</span>
      </header>
      <form class="analysis-form" @submit.prevent="submitLocate">
        <label>
          <span>原记录 ID</span>
          <input v-model.number="locateId" data-testid="locate-id" type="number" min="1" required />
        </label>
        <button class="btn" data-testid="submit-locate" type="submit" :disabled="busy">定位版本</button>
      </form>
      <div v-if="versions" data-testid="versions-result">
        <p class="muted">
          链路编号 {{ versions.chain }} · 当前版本记录 #{{ versions.current_record_id }} ·
          共 {{ versions.version_count }} 个版本
        </p>
        <table class="mini-table">
          <thead>
            <tr><th>记录 ID</th><th>版本</th><th>状态</th><th>生效时间</th><th>记录时间</th><th>说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="version in versions.versions" :key="version.id">
              <td>#{{ version.id }}</td>
              <td>v{{ version.版本 }}</td>
              <td>{{ version.status }}</td>
              <td>{{ version.生效时间 }}</td>
              <td>{{ version.记录时间 }}</td>
              <td>
                <span v-if="version.当前版本" class="tag tag-add">当前版本</span>
                <span v-if="version.已被取代" class="tag tag-warn">已被取代</span>
                <span v-if="version.已撤销" class="tag tag-del">已撤销</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import {
  backfillLineageEdge,
  compareVersionedLineage,
  locateLineageVersions,
  queryVersionedLineage,
} from '@/api/backend-service'
import type {
  LineageCompareResult,
  LineageModeResult,
  LineageQueryResult,
  LineageVersionsResult,
} from '@/data/types'

const form = ref({
  asOf: '2026-11-01',
  start: 'ODS订单',
  end: 'ADS销售报表',
  mode: 'both' as 'report' | 'remediation' | 'both',
  maxDepth: 6,
})
const result = ref<LineageQueryResult | null>(null)
const comparison = ref<LineageCompareResult | null>(null)
const versions = ref<LineageVersionsResult | null>(null)
const locateId = ref<number>(9)
const loading = ref(false)
const busy = ref(false)
const errorMessage = ref('')
const notice = ref('')
const backfillNotice = ref('')
const backfill = ref({
  上游实体: 'ODS订单',
  下游实体: 'DWS销售汇总',
  转换描述: '订单直连销售汇总（迟到补录）',
  生效时间: '2026-10-28',
  关联字段: 'order_id',
})

const panels = computed(() => {
  const payload = result.value
  if (!payload) return [] as { mode: 'report' | 'remediation'; label: string; data: LineageModeResult }[]
  return (['report', 'remediation'] as const)
    .map((mode) => {
      const data = payload.modes[mode]
      return data ? { mode, label: data.label, data } : null
    })
    .filter((item): item is { mode: 'report' | 'remediation'; label: string; data: LineageModeResult } => item !== null)
})

function summarize(payload: LineageQueryResult) {
  if (payload.reused) {
    notice.value = `同一时点的重复查询已复用 ${payload.query_id}，不会重复返回路径。`
  } else {
    notice.value = ''
  }
}

async function runQuery() {
  loading.value = true
  errorMessage.value = ''
  comparison.value = null
  try {
    const payload = await queryVersionedLineage({ ...form.value })
    result.value = payload
    summarize(payload)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '版本定界血缘查询失败'
  } finally {
    loading.value = false
  }
}

async function syncCompare() {
  busy.value = true
  errorMessage.value = ''
  try {
    if (!result.value) {
      const payload = await queryVersionedLineage({ ...form.value })
      result.value = payload
      summarize(payload)
    }
    comparison.value = await compareVersionedLineage({ ...form.value })
    notice.value = `已同步图谱比对待办 ${comparison.value.task.任务编号}`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '版本定界血缘比对失败'
  } finally {
    busy.value = false
  }
}

async function submitBackfill() {
  busy.value = true
  backfillNotice.value = ''
  errorMessage.value = ''
  try {
    const payload = await backfillLineageEdge({ ...backfill.value })
    if (!payload.ok) throw new Error(payload.message)
    backfillNotice.value = `${payload.message}。报告口径不变，整改口径可查到补录后的路径。`
  } catch (error) {
    backfillNotice.value = error instanceof Error ? error.message : '补录血缘链路失败'
  } finally {
    busy.value = false
  }
}

async function submitLocate() {
  busy.value = true
  errorMessage.value = ''
  try {
    versions.value = await locateLineageVersions(locateId.value)
  } catch (error) {
    versions.value = null
    errorMessage.value = error instanceof Error ? error.message : '血缘版本定位失败'
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.mode-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  gap: 12px;
  margin: 12px 0;
}
.mode-card,
.compare-block,
.backfill-block,
.locate-block {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 12px;
}
.mode-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 8px;
}
.mode-head h3 { margin: 0; font-size: 15px; }
.mode-count { color: var(--muted); font-size: 12px; }
.path-list { list-style: none; padding: 0; margin: 0 0 8px; }
.path-item { border: 1px solid #eef1f5; border-radius: 6px; padding: 8px 10px; margin-bottom: 8px; }
.path-head { display: flex; justify-content: space-between; gap: 12px; font-size: 13px; }
.path-meta { color: var(--muted); font-size: 12px; }
.mini-table { width: 100%; border-collapse: collapse; margin-top: 6px; }
.mini-table th,
.mini-table td { border: 1px solid #eef1f5; padding: 5px 8px; font-size: 12px; text-align: left; }
.muted { color: var(--muted); }
.excluded-block h4,
.diff-grid h4 { margin: 8px 0 4px; font-size: 13px; }
.excluded-block ul,
.diff-grid ul { margin: 0; padding-left: 18px; font-size: 12px; }
.excluded-block em { color: #b54708; font-style: normal; margin-left: 4px; }
.compare-summary { font-weight: 600; margin: 4px 0; }
.diff-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.tag { display: inline-block; padding: 0 6px; border-radius: 10px; background: #eef1f5; color: #475467; font-size: 11px; }
.tag-warn { background: #fef0c7; color: #b54708; }
.tag-add { background: #dcfae6; color: #067647; }
.tag-del { background: #fee4e2; color: #b42318; }
.notice-text { color: #067647; }
</style>
