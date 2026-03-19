import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../lib/api'
import OverrideModal from '../components/OverrideModal'

function fmtTime(ts) {
  return new Date(ts).toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function fmtAmount(n) {
  return '₹' + n.toLocaleString('en-IN')
}

const TH = 'font-condensed text-[11px] tracking-widest text-gray-500 text-left pb-2 pr-6'
const TD = 'py-3 pr-6 align-top'

export default function Inbox() {
  const queryClient = useQueryClient()
  const [expandedRow, setExpandedRow] = useState(null)
  const [overrideRow, setOverrideRow] = useState(null)
  const [rejectRow, setRejectRow] = useState(null)
  const [rejectNotes, setRejectNotes] = useState('Could not identify payer')

  const { data: payments = [], isLoading, error } = useQuery({
    queryKey: ['pending'],
    queryFn: () => api.get('/dashboard/pending').then((r) => r.data),
  })

  const approveMutation = useMutation({
    mutationFn: ({ paymentId, subscriberEmail }) =>
      api
        .post(`/dashboard/approve/${paymentId}`, { subscriber_email: subscriberEmail })
        .then((r) => r.data),
    onSuccess: () => {
      setExpandedRow(null)
      queryClient.invalidateQueries({ queryKey: ['pending'] })
      queryClient.invalidateQueries({ queryKey: ['summary'] })
    },
  })

  const rejectMutation = useMutation({
    mutationFn: ({ paymentId, notes }) =>
      api
        .post(`/dashboard/reject/${paymentId}`, { notes })
        .then((r) => r.data),
    onSuccess: () => {
      setRejectRow(null)
      setRejectNotes('Could not identify payer')
      queryClient.invalidateQueries({ queryKey: ['pending'] })
      queryClient.invalidateQueries({ queryKey: ['summary'] })
    },
  })

  if (isLoading) {
    return (
      <p className="font-mono text-gray-600 text-xs tracking-widest mt-10">LOADING...</p>
    )
  }

  if (error) {
    return (
      <p className="font-mono text-red-500 text-xs mt-10">
        ERROR: {error.message}
      </p>
    )
  }

  if (payments.length === 0) {
    return (
      <div className="mt-32 text-center">
        <p className="font-condensed text-gray-700 text-5xl tracking-[0.45em]">
          NO PENDING PAYMENTS
        </p>
      </div>
    )
  }

  return (
    <div>
      {/* Page header */}
      <div className="mb-6 flex items-baseline gap-4">
        <h1 className="font-condensed text-2xl tracking-[0.2em] text-white">INBOX</h1>
        <span className="font-mono text-xs text-gray-500">
          {payments.length} payment{payments.length !== 1 ? 's' : ''} awaiting resolution
        </span>
      </div>

      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-gray-800">
            {['TIME', 'PAYER', 'EMAIL', 'AMOUNT', 'STATUS', 'ACTION'].map((h) => (
              <th key={h} className={TH}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {payments.map((p) => (
            <PaymentRows
              key={p.payment_id}
              payment={p}
              expanded={expandedRow === p.payment_id}
              onToggleExpand={() => {
                const next = expandedRow === p.payment_id ? null : p.payment_id
                setExpandedRow(next)
                if (next) setRejectRow(null)
              }}
              onOverride={() => setOverrideRow(p)}
              approveMutation={approveMutation}
              isApproveError={
                approveMutation.isError &&
                approveMutation.variables?.paymentId === p.payment_id
              }
              approveErrorMsg={
                approveMutation.error?.response?.data?.detail ??
                approveMutation.error?.message
              }
              rejectExpanded={rejectRow === p.payment_id}
              onToggleReject={() => {
                const next = rejectRow === p.payment_id ? null : p.payment_id
                setRejectRow(next)
                if (next) setExpandedRow(null)
              }}
              rejectMutation={rejectMutation}
              isRejectError={
                rejectMutation.isError &&
                rejectMutation.variables?.paymentId === p.payment_id
              }
              rejectErrorMsg={
                rejectMutation.error?.response?.data?.detail ??
                rejectMutation.error?.message
              }
              rejectNotes={rejectRow === p.payment_id ? rejectNotes : ''}
              onRejectNotesChange={setRejectNotes}
              TD={TD}
            />
          ))}
        </tbody>
      </table>

      {overrideRow && (
        <OverrideModal
          payment={overrideRow}
          onClose={() => setOverrideRow(null)}
          onSuccess={() => setOverrideRow(null)}
        />
      )}
    </div>
  )
}

function PaymentRows({
  payment: p,
  expanded,
  onToggleExpand,
  onOverride,
  approveMutation,
  isApproveError,
  approveErrorMsg,
  rejectExpanded,
  onToggleReject,
  rejectMutation,
  isRejectError,
  rejectErrorMsg,
  rejectNotes,
  onRejectNotesChange,
  TD,
}) {
  const isReview = p.status === 'needs_review'
  const hasMatch = !!p.suggested_match

  return (
    <>
      {/* Main row */}
      <tr className="border-b border-gray-900 hover:bg-gray-900/20">
        <td className={`${TD} font-mono text-xs text-gray-400 whitespace-nowrap`}>
          {fmtTime(p.payment_timestamp)}
        </td>
        <td className={`${TD} font-mono text-sm text-gray-200`}>{p.name}</td>
        <td className={`${TD} font-mono text-xs text-gray-500`}>{p.email}</td>
        <td className={`${TD} font-mono text-xs text-gray-200`}>
          {fmtAmount(p.amount_inr)}
        </td>
        <td className={TD}>
          {isReview ? (
            <span className="font-condensed text-xs tracking-widest text-amber-500 border border-amber-500/30 px-2 py-0.5">
              REVIEW{hasMatch ? ` · ${p.suggested_match.confidence_score}%` : ''}
            </span>
          ) : (
            <span className="font-condensed text-xs tracking-widest text-red-500 border border-red-500/30 px-2 py-0.5">
              UNKNOWN
            </span>
          )}
        </td>
        <td className={`${TD} flex gap-2`}>
          {isReview && hasMatch && (
            <button
              onClick={onToggleExpand}
              className="font-condensed text-xs tracking-widest border border-gray-700 px-3 py-1 text-gray-300 hover:border-amber-500 hover:text-amber-500"
            >
              {expanded ? 'COLLAPSE' : 'CONFIRM'}
            </button>
          )}
          <button
            onClick={onOverride}
            className="font-condensed text-xs tracking-widest border border-gray-700 px-3 py-1 text-gray-500 hover:border-gray-400 hover:text-gray-200"
          >
            {p.status === 'unknown' ? 'RESOLVE' : 'OVERRIDE'}
          </button>
          <button
            onClick={onToggleReject}
            className="font-condensed text-xs tracking-widest border border-red-900/50 px-3 py-1 text-red-800 hover:border-red-700 hover:text-red-500"
          >
            REJECT
          </button>
        </td>
      </tr>

      {/* Inline suggested match expansion */}
      {expanded && hasMatch && (
        <tr className="border-b border-gray-800 bg-[#111]">
          <td colSpan={6} className="px-6 py-4">
            <div className="flex items-center justify-between">
              <div className="font-mono space-y-1">
                <p className="text-[10px] text-gray-600 tracking-widest">SUGGESTED MATCH</p>
                <p className="text-sm text-gray-100">{p.suggested_match.name}</p>
                <p className="text-xs text-gray-400">{p.suggested_match.email}</p>
                <p className="text-xs text-gray-600">
                  {p.suggested_match.substack_status?.toUpperCase()}
                  {p.suggested_match.expiry_date
                    ? ` · expires ${p.suggested_match.expiry_date}`
                    : ''}
                </p>
              </div>
              <div className="flex gap-3">
                {isApproveError && (
                  <p className="font-mono text-xs text-red-500 self-center">
                    {approveErrorMsg}
                  </p>
                )}
                <button
                  onClick={() =>
                    approveMutation.mutate({
                      paymentId: p.payment_id,
                      subscriberEmail: p.suggested_match.email,
                    })
                  }
                  disabled={approveMutation.isPending}
                  className="font-condensed text-xs tracking-widest bg-amber-500 text-black px-5 py-2 hover:bg-amber-400 disabled:opacity-40"
                >
                  {approveMutation.isPending ? 'CONFIRMING...' : 'CONFIRM MATCH'}
                </button>
                <button
                  onClick={onToggleExpand}
                  className="font-condensed text-xs tracking-widest border border-gray-700 px-4 py-2 text-gray-500 hover:text-gray-300"
                >
                  CANCEL
                </button>
              </div>
            </div>
          </td>
        </tr>
      )}

      {/* Inline reject panel */}
      {rejectExpanded && (
        <tr className="border-b border-gray-800 bg-[#111]">
          <td colSpan={6} className="px-6 py-4">
            <div className="flex items-center justify-between">
              <div className="font-mono space-y-2">
                <p className="text-[10px] text-gray-600 tracking-widest">REJECT PAYMENT</p>
                <p className="text-xs text-gray-500">
                  Payment will be marked failed and removed from the inbox.
                </p>
                <div className="flex items-center gap-3 mt-1">
                  <label className="font-condensed text-[10px] tracking-widest text-gray-600 whitespace-nowrap">
                    NOTES
                  </label>
                  <input
                    value={rejectNotes}
                    onChange={(e) => onRejectNotesChange(e.target.value)}
                    className="bg-transparent border border-gray-700 px-2 py-1 font-mono text-xs text-gray-300 focus:outline-none focus:border-gray-500 w-72"
                  />
                </div>
              </div>
              <div className="flex gap-3">
                {isRejectError && (
                  <p className="font-mono text-xs text-red-500 self-center">
                    {rejectErrorMsg}
                  </p>
                )}
                <button
                  onClick={() =>
                    rejectMutation.mutate({
                      paymentId: p.payment_id,
                      notes: rejectNotes || 'Rejected from dashboard',
                    })
                  }
                  disabled={rejectMutation.isPending}
                  className="font-condensed text-xs tracking-widest bg-red-800 text-white px-5 py-2 hover:bg-red-700 disabled:opacity-40"
                >
                  {rejectMutation.isPending ? 'REJECTING...' : 'CONFIRM REJECT'}
                </button>
                <button
                  onClick={onToggleReject}
                  className="font-condensed text-xs tracking-widest border border-gray-700 px-4 py-2 text-gray-500 hover:text-gray-300"
                >
                  CANCEL
                </button>
              </div>
            </div>
          </td>
        </tr>
      )}

      {/* Clarification email notice */}
      {p.clarification_sent && (
        <tr className="border-b border-gray-900">
          <td colSpan={6} className="pb-2 pl-3">
            <span className="font-mono text-[10px] text-gray-700">
              Clarification sent — awaiting response
              {p.clarification_resolved && ' · RESOLVED'}
            </span>
          </td>
        </tr>
      )}
    </>
  )
}
