import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../lib/api'

function fmtTime(ts) {
  if (!ts) return '—'
  return new Date(ts).toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

const TH = 'font-condensed text-[11px] tracking-widest text-gray-500 text-left pb-2 pr-6'
const TD = 'py-3 pr-6 align-top font-mono text-xs'

export default function Failed() {
  const queryClient = useQueryClient()
  const [expandedRow, setExpandedRow] = useState(null)
  const [retryingId, setRetryingId] = useState(null)

  const { data: actions = [], isLoading, error } = useQuery({
    queryKey: ['failed'],
    queryFn: () => api.get('/dashboard/failed').then((r) => r.data),
  })

  const retryMutation = useMutation({
    mutationFn: (actionId) =>
      api.post(`/admin/retry-action/${actionId}`).then((r) => r.data),
    onMutate: (actionId) => setRetryingId(actionId),
    onSuccess: () => {
      setRetryingId(null)
      queryClient.invalidateQueries({ queryKey: ['failed'] })
      queryClient.invalidateQueries({ queryKey: ['summary'] })
    },
    onError: () => setRetryingId(null),
  })

  if (isLoading) {
    return (
      <p className="font-mono text-xs text-gray-600 tracking-widest mt-10">LOADING...</p>
    )
  }

  if (error) {
    return (
      <p className="font-mono text-xs text-red-500 mt-10">ERROR: {error.message}</p>
    )
  }

  if (actions.length === 0) {
    return (
      <div className="mt-32 text-center">
        <p className="font-condensed text-gray-700 text-5xl tracking-[0.45em]">
          NO FAILED ACTIONS
        </p>
      </div>
    )
  }

  return (
    <div>
      <div className="mb-6 flex items-baseline gap-4">
        <h1 className="font-condensed text-2xl tracking-[0.2em] text-white">FAILED</h1>
        <span className="font-mono text-xs text-gray-500">
          {actions.length} failed action{actions.length !== 1 ? 's' : ''}
        </span>
      </div>

      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-gray-800">
            {['TIME', 'SUBSCRIBER', 'COMP', 'REASON', 'ACTION'].map((h) => (
              <th key={h} className={TH}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {actions.map((a) => (
            <>
              <tr
                key={a.id}
                onClick={() => setExpandedRow(expandedRow === a.id ? null : a.id)}
                className="border-b border-gray-900 hover:bg-gray-900/20 cursor-pointer"
              >
                <td className={`${TD} text-gray-400 whitespace-nowrap`}>
                  {fmtTime(a.created_at)}
                </td>
                <td className={`${TD} text-gray-300`}>{a.subscriber_email}</td>
                <td className={`${TD} text-gray-300`}>
                  {a.is_lifetime ? (
                    <span className="text-amber-500">LIFETIME</span>
                  ) : (
                    `${a.comp_days ?? '—'} days`
                  )}
                </td>
                <td className={`${TD} text-red-400 max-w-xs truncate`}>
                  {a.failure_reason ?? '—'}
                </td>
                <td className={TD} onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => retryMutation.mutate(a.id)}
                    disabled={retryingId === a.id}
                    className="font-condensed text-xs tracking-widest border border-gray-700 px-3 py-1 text-gray-400 hover:border-amber-500 hover:text-amber-500 disabled:opacity-40"
                  >
                    {retryingId === a.id ? 'RETRYING...' : 'RETRY'}
                  </button>
                </td>
              </tr>

              {/* Expanded detail */}
              {expandedRow === a.id && (
                <tr key={`${a.id}-exp`} className="border-b border-gray-800 bg-[#111]">
                  <td colSpan={5} className="px-6 py-5">
                    <div className="space-y-3">
                      {/* Failure reason prominent */}
                      <div>
                        <p className="font-condensed text-[10px] tracking-widest text-gray-600 mb-1">
                          FAILURE REASON
                        </p>
                        <p className="font-mono text-sm text-red-400">
                          {a.failure_reason ?? '—'}
                        </p>
                      </div>

                      {/* Detail grid */}
                      <div className="grid grid-cols-2 gap-6 font-mono text-xs mt-4">
                        <div className="space-y-2">
                          <Detail label="ACTION ID" value={a.id} />
                          <Detail label="SUBSCRIBER" value={a.subscriber_email} />
                          <Detail
                            label="COMP"
                            value={a.is_lifetime ? 'LIFETIME' : `${a.comp_days ?? '—'} days`}
                          />
                        </div>
                        <div className="space-y-2">
                          <Detail label="CREATED" value={fmtTime(a.created_at)} />
                          <Detail label="STATUS" value={a.execution_status?.toUpperCase()} valueClass="text-red-400" />
                        </div>
                      </div>

                      {/* Screenshot thumbnail */}
                      {a.screenshot_path && (
                        <div className="mt-4">
                          <p className="font-condensed text-[10px] tracking-widest text-gray-600 mb-2">
                            FAILURE SCREENSHOT
                          </p>
                          <img
                            src={`/screenshots/${a.screenshot_path.split('/').pop()}`}
                            alt="failure screenshot"
                            className="max-w-sm border border-gray-800 opacity-80 hover:opacity-100"
                          />
                        </div>
                      )}

                      {/* Retry error */}
                      {retryMutation.isError && retryMutation.variables === a.id && (
                        <p className="font-mono text-xs text-red-500 mt-2">
                          RETRY FAILED: {retryMutation.error?.message}
                        </p>
                      )}
                    </div>
                  </td>
                </tr>
              )}
            </>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Detail({ label, value, valueClass = 'text-gray-300' }) {
  return (
    <div className="flex gap-3">
      <span className="text-gray-600 w-24 shrink-0">{label}</span>
      <span className={valueClass}>{value}</span>
    </div>
  )
}
