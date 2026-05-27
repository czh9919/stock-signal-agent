<script setup>
import { ref, onMounted } from 'vue'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS, CategoryScale, LinearScale,
  PointElement, LineElement, Tooltip, Legend
} from 'chart.js'
import http from '../api'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend)

const runs = ref([])
const history = ref([])

onMounted(async () => {
  const [r, h] = await Promise.all([
    http.get('/runs'),
    http.get('/history'),
  ])
  runs.value = r.data
  history.value = h.data
})

function chartData(key, label, color) {
  return {
    labels: history.value.map(r => r.finished_at?.slice(0, 10)),
    datasets: [{
      label,
      data: history.value.map(r => r[key] != null ? Number(r[key]) : null),
      borderColor: color,
      backgroundColor: color + '22',
      tension: 0.3,
      spanGaps: true,
    }],
  }
}

const chartOpts = {
  responsive: true,
  plugins: { legend: { labels: { color: '#9ca3af', font: { size: 11 } } } },
  scales: {
    x: { ticks: { color: '#6b7280', maxTicksLimit: 10 } },
    y: { ticks: { color: '#6b7280' } },
  },
}

function ragClass(rag) {
  return { RED: 'text-red-400', AMBER: 'text-amber-400', GREEN: 'text-green-400' }[rag] || 'text-gray-400'
}
function fmtEur(v) {
  return v != null ? '€' + Number(v).toLocaleString('en-IE', { maximumFractionDigits: 0 }) : '—'
}
</script>

<template>
  <div class="space-y-6">
    <h2 class="text-lg font-semibold text-white">History</h2>

    <!-- Trend charts -->
    <div v-if="history.length" class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div class="bg-gray-900 border border-gray-800 rounded-xl p-4">
        <p class="text-xs text-gray-500 mb-3">NAV (€)</p>
        <Line :data="chartData('nav_eur', 'NAV €', '#3b82f6')" :options="chartOpts" />
      </div>
      <div class="bg-gray-900 border border-gray-800 rounded-xl p-4">
        <p class="text-xs text-gray-500 mb-3">VaR 95% (CF)</p>
        <Line :data="chartData('var_95_cf', 'VaR 95%', '#f59e0b')" :options="chartOpts" />
      </div>
      <div class="bg-gray-900 border border-gray-800 rounded-xl p-4">
        <p class="text-xs text-gray-500 mb-3">Sharpe Ratio</p>
        <Line :data="chartData('sharpe', 'Sharpe', '#10b981')" :options="chartOpts" />
      </div>
    </div>

    <!-- Run list -->
    <div class="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      <table class="w-full text-sm">
        <thead class="border-b border-gray-800">
          <tr class="text-xs text-gray-500">
            <th class="px-4 py-3 text-left">ID</th>
            <th class="px-4 py-3 text-left">Mode</th>
            <th class="px-4 py-3 text-left">Started</th>
            <th class="px-4 py-3 text-right">NAV</th>
            <th class="px-4 py-3 text-right">VaR 95%</th>
            <th class="px-4 py-3 text-right">Sharpe</th>
            <th class="px-4 py-3 text-center">RAG</th>
            <th class="px-4 py-3 text-center">Status</th>
            <th class="px-4 py-3"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in runs" :key="r.id"
            class="border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors">
            <td class="px-4 py-2.5 text-gray-500 text-xs">#{{ r.id }}</td>
            <td class="px-4 py-2.5 text-gray-300 capitalize">{{ r.run_mode }}</td>
            <td class="px-4 py-2.5 text-gray-400 text-xs">{{ r.started_at?.slice(0,16).replace('T',' ') }}</td>
            <td class="px-4 py-2.5 text-right text-gray-300">{{ fmtEur(r.nav_eur) }}</td>
            <td class="px-4 py-2.5 text-right text-gray-300">
              {{ r.var_95_cf != null ? (r.var_95_cf * 100).toFixed(1) + '%' : '—' }}
            </td>
            <td class="px-4 py-2.5 text-right text-gray-300">
              {{ r.sharpe != null ? Number(r.sharpe).toFixed(2) : '—' }}
            </td>
            <td class="px-4 py-2.5 text-center text-xs font-medium" :class="ragClass(r.rag_status)">
              {{ r.rag_status || '—' }}
            </td>
            <td class="px-4 py-2.5 text-center">
              <span :class="{
                'text-green-400': r.status === 'success',
                'text-red-400':   r.status === 'failed',
                'text-amber-400': r.status === 'running',
              }" class="text-xs">{{ r.status }}</span>
            </td>
            <td class="px-4 py-2.5 text-right">
              <a v-if="r.status === 'success'" :href="`/api/runs/${r.id}/report`" target="_blank"
                class="text-xs text-blue-400 hover:text-blue-300">View</a>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!runs.length" class="text-center text-sm text-gray-600 py-8">No runs yet</p>
    </div>
  </div>
</template>
