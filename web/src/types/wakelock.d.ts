export {}

declare global {
  interface WakeLockSentinel extends EventTarget {
    readonly released: boolean
    release(): Promise<void>
  }

  interface Navigator {
    wakeLock?: {
      request(type: 'screen'): Promise<WakeLockSentinel>
    }
  }
}
