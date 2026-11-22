import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import LineageVersionedView from '@/views/LineageVersionedView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/data_lineage/versioned', name: 'lineage-versioned', component: LineageVersionedView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
