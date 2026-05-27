<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useWsStore } from '../stores/ws'
import http from '../api'

const router = useRouter()
const ws = useWsStore()
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function login() {
  error.value = ''
  loading.value = true
  try {
    const form = new URLSearchParams()
    form.append('username', username.value)
    form.append('password', password.value)
    const { data } = await http.post('/auth/login', form,
      { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } })
    localStorage.setItem('token', data.access_token)
    ws.connect()
    router.push('/')
  } catch {
    error.value = 'Invalid username or password'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center bg-gray-950">
    <div class="w-full max-w-sm bg-gray-900 rounded-xl border border-gray-800 p-8">
      <h1 class="text-xl font-semibold text-white mb-6">Portfolio Risk Dashboard</h1>
      <form @submit.prevent="login" class="space-y-4">
        <div>
          <label class="block text-xs text-gray-400 mb-1">Username</label>
          <input v-model="username" type="text" required autocomplete="username"
            class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                   text-sm text-white focus:outline-none focus:border-blue-500" />
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">Password</label>
          <input v-model="password" type="password" required autocomplete="current-password"
            class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2
                   text-sm text-white focus:outline-none focus:border-blue-500" />
        </div>
        <p v-if="error" class="text-xs text-red-400">{{ error }}</p>
        <button type="submit" :disabled="loading"
          class="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50
                 text-white text-sm font-medium py-2 rounded-lg transition-colors">
          {{ loading ? 'Signing in…' : 'Sign in' }}
        </button>
      </form>
    </div>
  </div>
</template>
