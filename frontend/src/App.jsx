import { useState } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import AuthGate from './components/AuthGate'
import TopBar from './components/TopBar'
import Inbox from './views/Inbox'
import Log from './views/Log'
import Failed from './views/Failed'
import Settings from './views/Settings'
import api from './lib/api'

export default function App() {
  const [apiKey, setApiKey] = useState(() => localStorage.getItem('bridge_api_key'))

  // Validate stored key on load — if 403, the Axios interceptor clears it and reloads
  useQuery({
    queryKey: ['auth-check'],
    queryFn: () => api.get('/dashboard/summary').then((r) => r.data),
    enabled: !!apiKey,
    retry: false,
    staleTime: Infinity,
  })

  if (!apiKey) {
    return (
      <AuthGate
        onSuccess={(key) => {
          setApiKey(key)
        }}
      />
    )
  }

  return (
    <div className="min-h-screen bg-[#0f0f0f] text-gray-100">
      <TopBar />
      <main className="px-8 py-6">
        <Routes>
          <Route path="/" element={<Inbox />} />
          <Route path="/log" element={<Log />} />
          <Route path="/failed" element={<Failed />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  )
}
