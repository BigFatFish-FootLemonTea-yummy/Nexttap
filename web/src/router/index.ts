import { createRouter, createWebHistory } from 'vue-router'
import { useSessionStore } from '@/stores/session'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'upload',
      component: () => import('@/views/UploadView.vue'),
      meta: { title: '拍照上传' },
    },
    {
      path: '/result',
      name: 'result',
      component: () => import('@/views/ResultView.vue'),
      meta: { title: '定位结果', requiresResult: true },
    },
    {
      path: '/show',
      name: 'show',
      component: () => import('@/views/ShowView.vue'),
      meta: { title: '给老人看', requiresResult: true },
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach((to) => {
  if (to.meta.requiresResult) {
    const session = useSessionStore()
    if (!session.hasResult) return { name: 'upload' }
  }
  return true
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${String(to.meta.title)} · 指一下` : '指一下 · NextTap'
})

export default router
