<script setup>
import { useRouter } from 'vue-router'
import { useWsStore } from '../stores/ws'

const router = useRouter()
const ws = useWsStore()

function logout() {
  localStorage.removeItem('token')
  router.push('/login')
}

const links = [
  { to: '/',          label: 'Dashboard' },
  { to: '/history',   label: 'History' },
  { to: '/watchlist', label: 'Watchlist' },
  { to: '/config',    label: 'Config' },
]
</script>

<template>
  <nav class="bg-gray-900 border-b border-gray-800 px-4">
    <div class="max-w-7xl mx-auto flex items-center h-14 gap-6">
      <span class="font-semibold text-white tracking-tight">Portfolio Risk</span>
      <RouterLink v-for="l in links" :key="l.to" :to="l.to"
        class="text-sm text-gray-400 hover:text-white transition-colors"
        active-class="!text-white font-medium">
        {{ l.label }}
      </RouterLink>
      <div class="ml-auto flex items-center gap-3">
        <span v-if="ws.runStatus?.status === 'running'"
          class="flex items-center gap-1.5 text-xs text-amber-400">
          <span class="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
          Running
        </span>
        <button @click="logout"
          class="text-xs text-gray-500 hover:text-gray-300 transition-colors">
          Logout
        </button>
      </div>
    </div>
  </nav>
</template>
