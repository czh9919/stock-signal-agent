import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import './style.css'
import App from './App.vue'

import LoginView     from './views/LoginView.vue'
import DashboardView from './views/DashboardView.vue'
import HistoryView   from './views/HistoryView.vue'
import WatchlistView from './views/WatchlistView.vue'
import ConfigView    from './views/ConfigView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login',     component: LoginView },
    { path: '/',          component: DashboardView, meta: { auth: true } },
    { path: '/history',   component: HistoryView,   meta: { auth: true } },
    { path: '/watchlist', component: WatchlistView, meta: { auth: true } },
    { path: '/config',    component: ConfigView,    meta: { auth: true } },
  ],
})

router.beforeEach((to) => {
  if (to.meta.auth && !localStorage.getItem('token')) return '/login'
})

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
