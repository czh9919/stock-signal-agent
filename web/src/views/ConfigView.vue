<script setup>
import { ref, watch } from 'vue'
import http from '../api'

const files = ['settings', 'thresholds', 'volatility']
const active = ref('settings')
const content = ref('')
const saving = ref(false)
const saved = ref(false)
const error = ref('')

async function load(name) {
  try {
    const { data } = await http.get(`/config/${name}`)
    content.value = data
    error.value = ''
  } catch (e) {
    content.value = ''
    error.value = e.response?.data?.detail || 'Load failed'
  }
}

watch(active, load, { immediate: true })

async function save() {
  saving.value = true
  saved.value = false
  error.value = ''
  try {
    await http.put(`/config/${active.value}`, content.value, {
      headers: { 'Content-Type': 'text/plain' }
    })
    saved.value = true
    setTimeout(() => saved.value = false, 2000)
  } catch (e) {
    error.value = e.response?.data?.detail || 'Save failed'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold text-white">Config</h2>
      <button @click="save" :disabled="saving"
        class="px-3 py-1.5 text-xs bg-blue-700 hover:bg-blue-600 disabled:opacity-40
               text-white rounded-lg transition-colors font-medium">
        {{ saving ? 'Saving…' : saved ? 'Saved ✓' : 'Save' }}
      </button>
    </div>

    <div class="flex gap-2">
      <button v-for="f in files" :key="f" @click="active = f"
        :class="active === f ? 'bg-blue-700 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'"
        class="px-3 py-1.5 text-xs rounded-lg transition-colors capitalize">
        {{ f }}.yaml
      </button>
    </div>

    <p v-if="error" class="text-xs text-red-400">{{ error }}</p>

    <div class="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      <textarea v-model="content" spellcheck="false"
        class="w-full bg-transparent font-mono text-xs text-gray-300 p-4
               focus:outline-none resize-none"
        style="min-height: 60vh;" />
    </div>
    <p class="text-xs text-gray-600">Changes take effect on the next pipeline run.</p>
  </div>
</template>
