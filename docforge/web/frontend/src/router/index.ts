import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('@/components/layout/AppLayout.vue'),
    },
    {
      // Backward-compatible redirect: /viewer/:taskId -> /?task=:taskId
      path: '/viewer/:taskId',
      redirect: (to) => ({
        path: '/',
        query: { task: to.params.taskId as string },
      }),
    },
    {
      // Backward-compatible redirect
      path: '/verify/:taskId',
      redirect: (to) => ({
        path: '/',
        query: { task: to.params.taskId as string },
      }),
    },
    {
      // Compare route deprecated, redirect to home
      path: '/compare',
      redirect: '/',
    },
  ],
})

export default router
