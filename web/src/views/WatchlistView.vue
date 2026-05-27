<script setup>
import { ref, onMounted, computed } from 'vue'
import http from '../api'

// ── Watchlist state ──────────────────────────────────────────────────────────
const entries = ref([])
const saving = ref(false)
const saved = ref(false)

onMounted(async () => {
  const { data } = await http.get('/watchlist')
  entries.value = data
})

async function save() {
  saving.value = true
  saved.value = false
  try {
    await http.put('/watchlist', entries.value)
    saved.value = true
    setTimeout(() => saved.value = false, 2000)
  } catch (e) {
    alert(e.response?.data?.detail || 'Save failed')
  } finally {
    saving.value = false
  }
}

function removeRow(i) {
  entries.value.splice(i, 1)
}

// ── Search ───────────────────────────────────────────────────────────────────
const query = ref('')
const results = ref([])
const searching = ref(false)
let debounceTimer = null

async function onInput() {
  clearTimeout(debounceTimer)
  if (!query.value.trim()) { results.value = []; return }
  debounceTimer = setTimeout(async () => {
    searching.value = true
    try {
      const { data } = await http.get('/search', { params: { q: query.value } })
      results.value = data
    } finally {
      searching.value = false
    }
  }, 300)
}

function addTicker(stock) {
  const already = entries.value.some(e => e.ticker === stock.ticker)
  if (already) { query.value = ''; results.value = []; return }
  entries.value.push({
    ticker: stock.ticker,
    weight: 1.0,
    notes: stock.name,
    asset_class: 'equity',
    currency: 'USD',
  })
  query.value = ''
  results.value = []
}

const equities = computed(() => entries.value.filter(e => e.asset_class === 'equity'))
const others   = computed(() => entries.value.filter(e => e.asset_class !== 'equity'))
</script>

<template>
  <div class="space-y-5">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold text-white">Watchlist
        <span class="ml-2 text-sm font-normal text-gray-500">{{ entries.length }} tickers</span>
      </h2>
      <button @click="save" :disabled="saving"
        class="px-4 py-1.5 text-sm bg-blue-700 hover:bg-blue-600 disabled:opacity-40
               text-white rounded-lg transition-colors font-medium">
        {{ saving ? 'Saving…' : saved ? 'Saved ✓' : 'Save' }}
      </button>
    </div>

    <!-- Search box -->
    <div class="relative">
      <div class="flex items-center gap-2 bg-gray-900 border border-gray-700 rounded-xl px-4 py-2.5
                  focus-within:border-blue-500 transition-colors">
        <svg class="w-4 h-4 text-gray-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M21 21l-4.35-4.35M17 11A6 6 0 1 1 5 11a6 6 0 0 1 12 0z"/>
        </svg>
        <input v-model="query" @input="onInput" placeholder="Search S&P 500 by ticker or company name…"
          class="flex-1 bg-transparent text-sm text-white placeholder-gray-500 focus:outline-none" />
        <span v-if="searching" class="text-xs text-gray-500">Searching…</span>
      </div>

      <!-- Dropdown results -->
      <div v-if="results.length"
        class="absolute z-20 w-full mt-1 bg-gray-900 border border-gray-700 rounded-xl
               shadow-xl overflow-hidden max-h-72 overflow-y-auto">
        <button v-for="s in results" :key="s.ticker"
          @click="addTicker(s)"
          class="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-gray-800 transition-colors text-left">
          <span class="font-mono text-sm font-semibold text-blue-400 w-16 shrink-0">{{ s.ticker }}</span>
          <span class="text-sm text-gray-300 flex-1 truncate">{{ s.name }}</span>
          <span class="text-xs text-gray-600 shrink-0 truncate max-w-32">{{ s.sector }}</span>
          <span class="text-xs text-gray-600 shrink-0">+ Add</span>
        </button>
      </div>
    </div>

    <!-- Equities table -->
    <div class="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      <div class="px-4 py-2.5 border-b border-gray-800 flex items-center justify-between">
        <span class="text-xs font-medium text-gray-400">Equities (FF5 factor ranking)</span>
        <span class="text-xs text-gray-600">{{ equities.length }} stocks</span>
      </div>
      <table class="w-full text-sm">
        <thead>
          <tr class="text-xs text-gray-600 border-b border-gray-800">
            <th class="px-4 py-2 text-left">Ticker</th>
            <th class="px-4 py-2 text-left">Name</th>
            <th class="px-4 py-2 text-right">Weight</th>
            <th class="px-4 py-2"></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="row in equities" :key="row.ticker">
            <tr class="border-b border-gray-800/40 hover:bg-gray-800/20 transition-colors">
              <td class="px-4 py-2 font-mono text-blue-400 text-sm font-medium">{{ row.ticker }}</td>
              <td class="px-4 py-2">
                <input v-model="row.notes"
                  class="w-full bg-transparent text-sm text-gray-300 focus:outline-none
                         focus:bg-gray-800 rounded px-1 py-0.5 transition-colors" />
              </td>
              <td class="px-4 py-2 text-right">
                <input v-model.number="row.weight" type="number" step="0.1" min="0"
                  class="w-14 bg-gray-800 border border-gray-700 rounded px-2 py-0.5 text-xs
                         text-gray-300 text-right focus:outline-none focus:border-blue-500" />
              </td>
              <td class="px-4 py-2 text-right">
                <button @click="removeRow(entries.indexOf(row))"
                  class="text-gray-700 hover:text-red-400 transition-colors text-lg leading-none">×</button>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <p v-if="!equities.length" class="text-center text-sm text-gray-600 py-6">
        Search and add stocks above
      </p>
    </div>

    <!-- Bonds & Gold table -->
    <div v-if="others.length" class="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      <div class="px-4 py-2.5 border-b border-gray-800">
        <span class="text-xs font-medium text-gray-400">Bonds & Gold (diversification candidates)</span>
      </div>
      <table class="w-full text-sm">
        <tbody>
          <template v-for="row in others" :key="row.ticker">
            <tr class="border-b border-gray-800/40 hover:bg-gray-800/20 transition-colors">
              <td class="px-4 py-2 font-mono text-amber-400 text-sm font-medium w-20">{{ row.ticker }}</td>
              <td class="px-4 py-2">
                <input v-model="row.notes"
                  class="w-full bg-transparent text-sm text-gray-300 focus:outline-none
                         focus:bg-gray-800 rounded px-1 py-0.5" />
              </td>
              <td class="px-4 py-2 text-xs text-gray-600 capitalize">{{ row.asset_class }}</td>
              <td class="px-4 py-2 text-right">
                <button @click="removeRow(entries.indexOf(row))"
                  class="text-gray-700 hover:text-red-400 transition-colors text-lg leading-none">×</button>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <p class="text-xs text-gray-600">Changes take effect on the next pipeline run.</p>
  </div>
</template>
