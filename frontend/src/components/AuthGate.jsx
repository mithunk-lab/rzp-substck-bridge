import { useState } from 'react'
import api from '../lib/api'

export default function AuthGate({ onSuccess }) {
  const [key, setKey] = useState('')
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    if (!key.trim()) return

    setLoading(true)
    setError(null)

    // Temporarily store the key so the interceptor can attach it
    localStorage.setItem('bridge_api_key', key.trim())

    try {
      await api.get('/dashboard/summary')
      onSuccess(key.trim())
    } catch (err) {
      localStorage.removeItem('bridge_api_key')
      if (err.response?.status === 403 || err.response?.status === 401) {
        setError('INVALID KEY')
      } else {
        setError('CONNECTION ERROR — IS THE BACKEND RUNNING?')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#0f0f0f] flex flex-col items-center justify-center">
      <p className="font-condensed text-white text-2xl tracking-[0.4em] mb-1">
        WIRE BRIDGE
      </p>
      <p className="font-mono text-gray-600 text-xs tracking-widest mb-12">
        SUBSCRIPTION SYNC DASHBOARD
      </p>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4 w-80">
        <label className="font-condensed text-gray-500 text-xs tracking-[0.3em]">
          DASHBOARD ACCESS KEY
        </label>
        <input
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          autoFocus
          className="bg-transparent border border-gray-700 rounded px-3 py-2 font-mono text-sm text-gray-200 focus:outline-none focus:border-gray-400"
          placeholder="sk-..."
        />
        {error && (
          <p className="font-mono text-xs text-red-500 tracking-widest">{error}</p>
        )}
        <button
          type="submit"
          disabled={loading || !key.trim()}
          className="font-condensed tracking-[0.3em] text-sm bg-gray-800 border border-gray-700 text-gray-200 py-2 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {loading ? 'VERIFYING...' : 'ACCESS DASHBOARD'}
        </button>
      </form>
    </div>
  )
}
