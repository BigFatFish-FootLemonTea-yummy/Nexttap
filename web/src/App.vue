<script setup lang="ts">
import { RouterView, useRoute } from 'vue-router'
import { computed } from 'vue'

const route = useRoute()
// 全屏给老人看时不带应用外壳，避免任何可点元素干扰
const bare = computed(() => route.name === 'show')
</script>

<template>
  <div v-if="bare" class="min-h-screen">
    <RouterView />
  </div>
  <div v-else class="mx-auto flex min-h-screen w-full max-w-xl flex-col px-4 pb-safe pt-safe">
    <header class="flex items-center gap-2 py-3">
      <span class="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-lg font-bold text-primary-foreground" aria-hidden="true">指</span>
      <div class="leading-tight">
        <p class="text-base font-semibold">指一下 <span class="text-xs font-normal text-muted-foreground">NextTap</span></p>
        <p class="text-xs text-muted-foreground">拍下屏幕，告诉老人下一步点哪</p>
      </div>
    </header>
    <main class="flex flex-1 flex-col">
      <RouterView />
    </main>
    <footer class="py-4 text-center text-xs text-muted-foreground">
      截图仅在你的设备与后端之间处理，不用于其他用途
    </footer>
  </div>
</template>
