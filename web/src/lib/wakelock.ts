import { onBeforeUnmount, onMounted, ref } from 'vue'

/**
 * 防熄屏（定稿 F5：Wake Lock）。
 * iOS Safari 不支持时静默降级；切到后台锁会自动释放，回到前台要重新申请。
 * 不用 Fullscreen API：iOS 上 requestFullscreen 对普通元素无效（交接文档第八节）。
 */
export function useWakeLock() {
  const active = ref(false)
  const supported = typeof navigator !== 'undefined' && Boolean(navigator.wakeLock)
  let sentinel: WakeLockSentinel | null = null
  let wanted = false

  async function acquire() {
    if (!supported || !wanted || document.visibilityState !== 'visible') return
    try {
      sentinel = await navigator.wakeLock!.request('screen')
      active.value = true
      sentinel.addEventListener('release', () => {
        active.value = false
      })
    } catch {
      active.value = false
    }
  }

  async function release() {
    wanted = false
    try {
      await sentinel?.release()
    } catch {
      /* 已释放 */
    }
    sentinel = null
    active.value = false
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') void acquire()
  }

  onMounted(() => {
    wanted = true
    void acquire()
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onBeforeUnmount(() => {
    document.removeEventListener('visibilitychange', onVisibilityChange)
    void release()
  })

  return { active, supported, release }
}
