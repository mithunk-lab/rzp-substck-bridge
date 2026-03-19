import { Fragment, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
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

function CompCell({ comp_days, is_lifetime }) {
  if (is_lifetime) return <span className="text-amber-500">LIFETIME</span>
  if (comp_days) return <span>{comp_days} days</span>
  return <span className="text-gray-600">—</span>
}

function StatusBadge({ status }) {
  const map = {
    success: 'text-green-500',
    failed: 'text-red-500',
    manual: 'text-gray-400',
    pending: 'text-amber-500',
  }
  return (
    <span className={`font-condensed text-xs tracking-widest ${map[status] ?? 'text-gray-500'}`}>
      {status?.toUpperCase()}
    </span>
  )
}

const TH = 'font-condensed text-[11px] tracking-widest text-gray-500 text-left pb-2 pr-6'
const TD = 'py-3 pr-6 align-top font-mono text-xs'

export default function Log() {
  const [filters, setFilters] = useState({
    status: '',
    email: '',
    date_from: '',
    date_to: '',
  })
  const [page, setPage] = useState(1)
  const [expandedRow, setExpandedRow] = useState(null)
  const [exportLoading, setExportLoading] = useState(false)
  const [exportError, setExportError] = useState(null)

  const params = {
    page,
    page_size: 50,
    ...(filters.status && { status: filters.status }),
    ...(filters.email && { email: filters.email }),
    ...(filters.date_from && { date_from: filters.date_from }),
    ...(filters.date_to && { date_to: filters.date_to }),
  }

  const { data, isLoading, error } = useQuery({
    queryKey: ['log', params],
    queryFn: () => api.get('/dashboard/log', { params }).then((r) => r.data),
    placeholderData: (prev) => prev,
  })

  function handleFilterChange(key, value) {
    setFilters((f) => ({ ...f, [key]: value }))
    setPage(1)
  }

  function handleExport() {
    const exportParams = {}
    if (filters.status) exportParams.status = filters.status
    if (filters.email) exportParams.email = filters.email
    if (filters.date_from) exportParams.date_from = filters.date_from
    if (filters.date_to) exportParams.date_to = filters.date_to

    setExportLoading(true)
    setExportError(null)
    api
      .get('/dashboard/log/export', { params: exportParams, responseType: 'blob' })
      .then((r) => {
        const url = URL.createObjectURL(r.data)
        const a = document.createElement('a')
        a.href = url
        a.download = 'action_log.csv'
        a.click()
        URL.revokeObjectURL(url)
      })
      .catch((err) => {
        setExportError(err.response?.data?.detail ?? err.message ?? 'Export failed')
      })
      .finally(() => setExportLoading(false))
  }

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const pages = data?.pages ?? 1

  return (
    <div>
      {/* Page header */}
      <div className="mb-5 flex items-center justify-between">
        <h1 className="font-condensed text-2xl tracking-[0.2em] text-white">LOG</h1>
        <div className="flex flex-col items-end gap-1">
          <button
            onClick={handleExport}
            disabled={exportLoading}
            className="font-condensed text-xs tracking-widest border border-gray-700 px-4 py-1.5 text-gray-400 hover:border-gray-500 hover:text-gray-200 disabled:opacity-40"
          >
            {exportLoading ? 'EXPORTING...' : 'EXPORT CSV'}
          </button>
          {exportError && (
            <p className="font-mono text-[10px] text-red-500">{exportError}</p>
          )}
        </div>
      </div>

      {/* Filter bar */}
      <div className="flex gap-4 mb-5 flex-wrap">
        <div className="flex flex-col gap-1">
          <label className="font-condensed text-[10px] tracking-widest text-gray-600">FROM</label>
          <input
            type="date"
            value={filters.date_from}
            onChange={(e) => handleFilterChange('date_from', e.target.value)}
            className="bg-transparent border border-gray-700 rounded px-2 py-1 font-mono text-xs text-gray-300 focus:outline-none focus:border-gray-500"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="font-condensed text-[10px] tracking-widest text-gray-600">TO</label>
          <input
            type="date"
            value={filters.date_to}
            onChange={(e) => handleFilterChange('date_to', e.target.value)}
            className="bg-transparent border border-gray-700 rounded px-2 py-1 font-mono text-xs text-gray-300 focus:outline-none focus:border-gray-500"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="font-condensed text-[10px] tracking-widest text-gray-600">STATUS</label>
          <select
            value={filters.status}
            onChange={(e) => handleFilterChange('status', e.target.value)}
            className="bg-[#0f0f0f] border border-gray-700 rounded px-2 py-1 font-mono text-xs text-gray-300 focus:outline-none focus:border-gray-500"
          >
            <option value="">ALL</option>
            <option value="success">SUCCESS</option>
            <option value="failed">FAILED</option>
            <option value="manual">MANUAL</option>
            <option value="pending">PENDING</option>
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="font-condensed text-[10px] tracking-widest text-gray-600">EMAIL</label>
          <input
            type="text"
            value={filters.email}
            onChange={(e) => handleFilterChange('email', e.target.value)}
            placeholder="filter by email..."
            className="bg-transparent border border-gray-700 rounded px-2 py-1 font-mono text-xs text-gray-300 placeholder-gray-700 focus:outline-none focus:border-gray-500"
          />
        </div>
      </div>

      {isLoading && (
        <p className="font-mono text-xs text-gray-600 tracking-widest mt-4">LOADING...</p>
      )}
      {error && (
        <p className="font-mono text-xs text-red-500 mt-4">ERROR: {error.message}</p>
      )}

      {!isLoading && (
        <>
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-gray-800">
                {['TIME', 'PAYER', 'SUBSCRIBER', 'AMOUNT', 'COMP', 'STATUS'].map((h) => (
                  <th key={h} className={TH}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.length === 0 && (
                <tr>
                  <td colSpan={6} className="font-mono text-xs text-gray-600 py-8 tracking-widest">
                    NO RECORDS FOUND
                  </td>
                </tr>
              )}
              {items.map((item) => (
                <Fragment key={item.id}>
                  <tr
                    onClick={() =>
                      setExpandedRow(expandedRow === item.id ? null : item.id)
                    }
                    className="border-b border-gray-900 hover:bg-gray-900/20 cursor-pointer"
                  >
                    <td className={`${TD} text-gray-400 whitespace-nowrap`}>
                      {fmtTime(item.created_at)}
                    </td>
                    <td className={`${TD} text-gray-300`}>{item.payment?.name}</td>
                    <td className={`${TD} text-gray-400`}>{item.subscriber_email}</td>
                    <td className={`${TD} text-gray-200`}>
                      ₹{item.payment?.amount_inr?.toLocaleString('en-IN')}
                    </td>
                    <td className={`${TD} text-gray-300`}>
                      <CompCell comp_days={item.comp_days} is_lifetime={item.is_lifetime} />
                    </td>
                    <td className={TD}>
                      <StatusBadge status={item.execution_status} />
                    </td>
                  </tr>

                  {/* Expanded detail row */}
                  {expandedRow === item.id && (
                    <tr className="border-b border-gray-800 bg-[#111]">
                      <td colSpan={6} className="px-6 py-4">
                        <div className="grid grid-cols-2 gap-6 font-mono text-xs">
                          <div className="space-y-2">
                            <Detail label="ACTION ID" value={item.id} />
                            <Detail label="RAZORPAY ID" value={item.payment?.razorpay_payment_id} />
                            <Detail label="PAYER EMAIL" value={item.payment?.email} />
                            <Detail label="PAYMENT TIME" value={fmtTime(item.payment?.payment_timestamp)} />
                          </div>
                          <div className="space-y-2">
                            <Detail label="SUBSCRIBER" value={item.subscriber_email} />
                            <Detail label="COMP" value={item.is_lifetime ? 'LIFETIME' : `${item.comp_days} days`} />
                            <Detail label="EXECUTED AT" value={item.executed_at ? fmtTime(item.executed_at) : '—'} />
                            {item.failure_reason && (
                              <Detail label="FAILURE" value={item.failure_reason} valueClass="text-red-400" />
                            )}
                          </div>
                        </div>
                        {item.screenshot_path && (
                          <div className="mt-4">
                            <p className="font-condensed text-[10px] tracking-widest text-gray-600 mb-2">
                              SCREENSHOT
                            </p>
                            <img
                              src={`${import.meta.env.VITE_API_URL || ''}/screenshots/${item.screenshot_path.split('/').pop()}`}
                              alt="execution screenshot"
                              className="max-w-xs border border-gray-700 opacity-80 hover:opacity-100"
                            />
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>

          {/* Pagination */}
          {pages > 1 && (
            <div className="flex items-center justify-between mt-6 font-mono text-xs text-gray-500">
              <span>{total} total records</span>
              <div className="flex items-center gap-4">
                <button
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page === 1}
                  className="hover:text-gray-200 disabled:opacity-30"
                >
                  ← PREVIOUS
                </button>
                <span className="text-gray-400">
                  PAGE {page} OF {pages}
                </span>
                <button
                  onClick={() => setPage((p) => Math.min(pages, p + 1))}
                  disabled={page === pages}
                  className="hover:text-gray-200 disabled:opacity-30"
                >
                  NEXT →
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function Detail({ label, value, valueClass = 'text-gray-300' }) {
  return (
    <div className="flex gap-3">
      <span className="text-gray-600 w-28 shrink-0">{label}</span>
      <span className={valueClass}>{value}</span>
    </div>
  )
}
