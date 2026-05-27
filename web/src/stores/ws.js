import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useWsStore = defineStore('ws', () => {
  const logs = ref([])
  const runStatus = ref(null)
  const prices = ref([])
  let socket = null

  function connect() {
    const token = localStorage.getItem('token')
    if (!token || socket?.readyState === WebSocket.OPEN) return

    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const host = import.meta.env.VITE_API_HOST || location.host
    socket = new WebSocket(`${proto}://${host}/ws?token=${token}`)

    socket.onmessage = ({ data }) => {
      const msg = JSON.parse(data)
      if (msg.type === 'log') {
        logs.value.push(msg.data)
        if (logs.value.length > 500) logs.value.shift()
      } else if (msg.type === 'run_status') {
        runStatus.value = msg.data
      } else if (msg.type === 'price_update') {
        prices.value = msg.data
      }
    }

    socket.onclose = () => {
      socket = null
      setTimeout(connect, 5000)
    }
  }

  function clearLogs() { logs.value = [] }

  return { logs, runStatus, prices, connect, clearLogs }
})
