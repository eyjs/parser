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
      path: '/verify/:taskId',
      name: 'verify',
      component: () => import('@/views/VerifyView.vue'),
      meta: { fullWidth: true },
    },
    {
      path: '/compare',
      name: 'compare',
      component: () => import('@/views/CompareView.vue'),
      meta: { fullWidth: true },
    },
  ],
})

export default router
