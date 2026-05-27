<script setup>
import { onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useWsStore } from './stores/ws'
import NavBar from './components/NavBar.vue'

const route = useRoute()
const ws = useWsStore()

onMounted(() => {
  if (localStorage.getItem('token')) ws.connect()
})
</script>

<template>
  <div class="min-h-screen bg-gray-950">
    <NavBar v-if="route.path !== '/login'" />
    <main :class="route.path !== '/login' ? 'max-w-7xl mx-auto px-4 py-6' : ''">
      <RouterView />
    </main>
  </div>
</template>
