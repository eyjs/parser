import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      component: () => import('@/views/DashboardView.vue'),
    },
    {
      path: '/viewer/:taskId',
      name: 'viewer',
      component: () => import('@/views/ViewerView.vue'),
      meta: { fullWidth: true },
    },
    {
      // Backward-compatible redirect
      path: '/verify/:taskId',
      redirect: (to) => `/viewer/${to.params.taskId}`,
    },
    {
      // Compare route deprecated, redirect to dashboard
      path: '/compare',
      redirect: '/',
    },
  ],
})

export default router
