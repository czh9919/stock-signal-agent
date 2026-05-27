<script setup>
import { ref, onMounted, computed, watch } from 'vue'
import { useWsStore } from '../stores/ws'
import http from '../api'

const ws = useWsStore()
const latestRun = ref(null)
const loading = ref(true)
const triggering = ref(false)
const showLogs = ref(false)
const reportLang = ref('en')

onMounted(async () => {
  try {
    const { data } = await http.get('/runs/latest')
    latestRun.value = data
  } catch {}
  loading.value = false
})

watch(() => ws.runStatus, async (s) => {
  if (s?.status === 'success') {
    const { data } = await http.get('/runs/latest')
    latestRun.value = data
  }
})

async function trigger(mode) {
  triggering.value = true
  ws.clearLogs()
  showLogs.value = true
  try {
    await http.post(`/run/${mode}`)
  } catch (e) {
    alert(e.response?.data?.detail || 'Failed to trigger run')
  } finally {
    triggering.value = false
  }
}

const rag = computed(() => latestRun.value?.rag_status || 'GREEN')
const ragColor = computed(() => ({
  RED: 'text-red-400', AMBER: 'text-amber-400', GREEN: 'text-green-400'
})[rag.value] || 'text-gray-400')

function fmt(v, digits = 2) {
  if (v == null) return '—'
  return Number(v).toFixed(digits)
}
function fmtEur(v) {
  if (v == null) return '—'
  return '€' + Number(v).toLocaleString('en-IE', { maximumFractionDigits: 0 })
}

const reportSrc = computed(() => {
  if (!latestRun.value?.id) return null
  return `/api/runs/${latestRun.value.id}/report?lang=${reportLang.value}`
})

const isRunning = computed(() => ws.runStatus?.status === 'running')
</script>

<template>
  <div class="space-y-6">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold text-white">Dashboard</h2>
      <div class="flex gap-2">
        <button @click="trigger('alert_check')" :disabled="triggering || isRunning"
          class="px-3 py-1.5 text-xs bg-gray-800 hover:bg-gray-700 disabled:opacity-40
                 text-gray-300 rounded-lg transition-colors">
          Alert Check
        </button>
        <button @click="trigger('portfolio')" :disabled="triggering || isRunning"
          class="px-3 py-1.5 text-xs bg-gray-800 hover:bg-gray-700 disabled:opacity-40
                 text-gray-300 rounded-lg transition-colors">
          Portfolio
        </button>
        <button @click="trigger('full')" :disabled="triggering || isRunning"
          class="px-3 py-1.5 text-xs bg-blue-700 hover:bg-blue-600 disabled:opacity-40
                 text-white rounded-lg transition-colors font-medium">
          Full Run
        </button>
      </div>
    </div>

    <!-- Metric cards -->
    <div v-if="!loading" class="grid grid-cols-2 md:grid-cols-4 gap-3">
      <div class="bg-gray-900 border border-gray-800 rounded-xl p-4">
        <p class="text-xs text-gray-500 mb-1">NAV</p>
        <p class="text-xl font-semibold text-white">{{ fmtEur(latestRun?.nav_eur) }}</p>
      </div>
      <div class="bg-gray-900 border border-gray-800 rounded-xl p-4">
        <p class="text-xs text-gray-500 mb-1">VaR 95% (CF)</p>
        <p class="text-xl font-semibold text-white">
          {{ latestRun?.var_95_cf != null ? (latestRun.var_95_cf * 100).toFixed(1) + '%' : '—' }}
        </p>
      </div>
      <div class="bg-gray-900 border border-gray-800 rounded-xl p-4">
        <p class="text-xs text-gray-500 mb-1">Sharpe</p>
        <p class="text-xl font-semibold text-white">{{ fmt(latestRun?.sharpe) }}</p>
      </div>
      <div class="bg-gray-900 border border-gray-800 rounded-xl p-4">
        <p class="text-xs text-gray-500 mb-1">Status</p>
        <p class="text-xl font-semibold" :class="ragColor">{{ rag }}</p>
      </div>
    </div>

    <!-- Live prices -->
    <div v-if="ws.prices.length" class="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <div class="flex items-center gap-2 mb-3">
        <p class="text-xs font-medium text-gray-400">Live Prices</p>
        <span class="text-xs text-gray-600">(15-20 min delay)</span>
        <span class="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse ml-auto" />
      </div>
      <div class="grid grid-cols-3 sm:grid-cols-5 md:grid-cols-7 gap-2">
        <div v-for="p in ws.prices" :key="p.ticker"
          class="bg-gray-800 rounded-lg px-2 py-1.5 text-center">
          <p class="text-xs text-gray-400">{{ p.ticker }}</p>
          <p class="text-sm font-medium text-white">${{ Number(p.price).toFixed(2) }}</p>
        </div>
      </div>
    </div>

    <!-- Log stream -->
    <div v-if="showLogs" class="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <div class="flex items-center justify-between mb-2">
        <p class="text-xs font-medium text-gray-400">Pipeline Log</p>
        <button @click="showLogs = false" class="text-xs text-gray-600 hover:text-gray-400">hide</button>
      </div>
      <div class="bg-gray-950 rounded-lg p-3 h-48 overflow-y-auto font-mono text-xs text-gray-400 space-y-0.5">
        <p v-for="(line, i) in ws.logs" :key="i"
          :class="line.includes('[ERROR]') || line.includes('[WARNING]') ? 'text-amber-400' : ''">
          {{ line }}
        </p>
        <p v-if="!ws.logs.length" class="text-gray-600">Waiting for logs…</p>
      </div>
    </div>

    <!-- Latest report -->
    <div v-if="latestRun?.id" class="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      <div class="flex items-center justify-between px-4 py-3 border-b border-gray-800">
        <p class="text-xs font-medium text-gray-400">
          Latest Report
          <span class="text-gray-600 ml-2">{{ latestRun.finished_at?.slice(0,16).replace('T',' ') }} UTC</span>
        </p>
        <div class="flex gap-1">
          <button @click="reportLang = 'en'"
            :class="reportLang === 'en' ? 'bg-blue-700 text-white' : 'bg-gray-800 text-gray-400'"
            class="text-xs px-2 py-1 rounded transition-colors">EN</button>
          <button @click="reportLang = 'zh'"
            :class="reportLang === 'zh' ? 'bg-blue-700 text-white' : 'bg-gray-800 text-gray-400'"
            class="text-xs px-2 py-1 rounded transition-colors">中文</button>
        </div>
      </div>
      <iframe v-if="reportSrc" :src="reportSrc"
        class="w-full border-0 bg-white" style="height:75vh;" />
    </div>

    <div v-if="!loading && !latestRun"
      class="bg-gray-900 border border-gray-800 rounded-xl p-12 text-center text-gray-500 text-sm">
      No reports yet. Click <strong class="text-gray-300">Full Run</strong> to generate the first one.
    </div>
  </div>
</template>
