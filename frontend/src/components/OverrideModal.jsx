import { useState, useEffect, useRef } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../lib/api'

function useDebounce(value, delay = 300) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return debounced
}

export default function OverrideModal({ payment, onClose, onSuccess }) {
  const queryClient = useQueryClient()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [searching, setSearching] = useState(false)
  const [selected, setSelected] = useState(null)
  const [searchError, setSearchError] = useState(null)
  const inputRef = useRef(null)
  const debouncedQuery = useDebounce(query, 300)

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  // Close on Escape
  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // Real-time search
  useEffect(() => {
    if (debouncedQuery.length < 2) {
      setResults([])
      return
    }
    setSearching(true)
    setSearchError(null)
    api
      .get('/dashboard/subscribers/search', { params: { q: debouncedQuery } })
      .then((r) => setResults(r.data))
      .catch(() => setSearchError('SEARCH FAILED'))
      .finally(() => setSearching(false))
  }, [debouncedQuery])

  const approveMutation = useMutation({
    mutationFn: (subscriberEmail) =>
      api
        .post(`/dashboard/approve/${payment.payment_id}`, {
          subscriber_email: subscriberEmail,
        })
        .then((r) => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pending'] })
      queryClient.invalidateQueries({ queryKey: ['summary'] })
      onSuccess()
    },
  })

  const statusLabel = (s) => {
    if (s === 'active') return 'ACTIVE'
    if (s === 'lapsed') return 'LAPSED'
    if (s === 'lifetime') return 'LIFETIME'
    return s?.toUpperCase()
  }

  const statusColor = (s) => {
    if (s === 'active') return 'text-green-500'
    if (s === 'lifetime') return 'text-amber-500'
    return 'text-gray-500'
  }

  return (
    // Backdrop
    <div
      className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-[#0f0f0f] border border-gray-700 w-[560px] max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-800 flex items-baseline justify-between">
          <div>
            <p className="font-condensed text-white tracking-[0.25em] text-base">
              OVERRIDE MATCH
            </p>
            <p className="font-mono text-xs text-gray-500 mt-0.5">
              {payment.email} · ₹{payment.amount_inr?.toLocaleString('en-IN')}
            </p>
          </div>
          <button
            onClick={onClose}
            className="font-mono text-gray-600 hover:text-gray-300 text-xs"
          >
            ESC
          </button>
        </div>

        {/* Search */}
        <div className="px-6 py-4 border-b border-gray-800">
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setSelected(null)
            }}
            placeholder="Search by name or email..."
            className="w-full bg-transparent border border-gray-700 rounded px-3 py-2 font-mono text-sm text-gray-200 focus:outline-none focus:border-gray-400 placeholder-gray-700"
          />
          {searching && (
            <p className="font-mono text-[10px] text-gray-600 mt-1 tracking-widest">
              SEARCHING...
            </p>
          )}
          {searchError && (
            <p className="font-mono text-[10px] text-red-500 mt-1">{searchError}</p>
          )}
        </div>

        {/* Results */}
        <div className="flex-1 overflow-y-auto">
          {results.length === 0 && debouncedQuery.length >= 2 && !searching && (
            <p className="font-mono text-xs text-gray-600 px-6 py-4 tracking-widest">
              NO RESULTS
            </p>
          )}
          {results.map((sub) => (
            <button
              key={sub.email}
              onClick={() => setSelected(sub)}
              className={[
                'w-full text-left px-6 py-3 border-b border-gray-900 flex items-center justify-between',
                selected?.email === sub.email
                  ? 'bg-gray-800'
                  : 'hover:bg-gray-900/60',
              ].join(' ')}
            >
              <div>
                <p className="font-mono text-sm text-gray-200">{sub.name}</p>
                <p className="font-mono text-xs text-gray-500">{sub.email}</p>
              </div>
              <div className="text-right">
                <p className={`font-condensed text-xs tracking-widest ${statusColor(sub.substack_status)}`}>
                  {statusLabel(sub.substack_status)}
                </p>
                {sub.expiry_date && (
                  <p className="font-mono text-[10px] text-gray-600">
                    exp {sub.expiry_date}
                  </p>
                )}
              </div>
            </button>
          ))}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-gray-800 flex items-center justify-between">
          <div>
            {selected && (
              <p className="font-mono text-xs text-gray-400">
                Selected:{' '}
                <span className="text-amber-500">{selected.email}</span>
              </p>
            )}
            {approveMutation.isError && (
              <p className="font-mono text-xs text-red-500 mt-1">
                ERROR: {approveMutation.error?.response?.data?.detail ?? approveMutation.error?.message}
              </p>
            )}
          </div>
          <div className="flex gap-3">
            <button
              onClick={onClose}
              className="font-condensed text-xs tracking-widest border border-gray-700 px-4 py-2 text-gray-500 hover:text-gray-300"
            >
              CANCEL
            </button>
            <button
              onClick={() => selected && approveMutation.mutate(selected.email)}
              disabled={!selected || approveMutation.isPending}
              className="font-condensed text-xs tracking-widest bg-amber-500 text-black px-5 py-2 hover:bg-amber-400 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {approveMutation.isPending ? 'CONFIRMING...' : 'CONFIRM'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
