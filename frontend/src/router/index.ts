import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import DataPartitionView from '@/views/DataPartitionView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/data_partition', name: 'data_partition', component: DataPartitionView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
