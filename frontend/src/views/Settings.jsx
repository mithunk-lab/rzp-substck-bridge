import { useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../lib/api'

function fmtTime(ts) {
  if (!ts) return 'NEVER'
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

function maskKey(key) {
  if (!key || key.length < 8) return '••••••••'
  return key.slice(0, 4) + '••••••••' + key.slice(-4)
}

export default function Settings() {
  const queryClient = useQueryClient()
  const fileRef = useRef(null)
  const [uploadResult, setUploadResult] = useState(null)
  const [uploadError, setUploadError] = useState(null)

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get('/dashboard/settings').then((r) => r.data),
  })

  const syncMutation = useMutation({
    mutationFn: (file) => {
      const form = new FormData()
      form.append('file', file)
      return api
        .post('/admin/sync-subscribers', form, {
          headers: { 'Content-Type': 'multipart/form-data' },
        })
        .then((r) => r.data)
    },
    onSuccess: (data) => {
      setUploadResult(data)
      setUploadError(null)
      queryClient.invalidateQueries({ queryKey: ['settings'] })
      queryClient.invalidateQueries({ queryKey: ['summary'] })
    },
    onError: (err) => {
      setUploadError(err.response?.data?.detail ?? err.message)
      setUploadResult(null)
    },
  })

  function handleFileChange(e) {
    const file = e.target.files[0]
    if (file) {
      setUploadResult(null)
      setUploadError(null)
      syncMutation.mutate(file)
    }
    // Reset file input so the same file can be re-uploaded
    e.target.value = ''
  }

  const apiKey = localStorage.getItem('bridge_api_key') ?? ''

  if (isLoading) {
    return (
      <p className="font-mono text-xs text-gray-600 tracking-widest mt-10">LOADING...</p>
    )
  }

  return (
    <div className="max-w-2xl">
      <h1 className="font-condensed text-2xl tracking-[0.2em] text-white mb-8">SETTINGS</h1>

      {/* ── Section 1: Subscriber Sync ─────────────────────────────── */}
      <section className="mb-8">
        <h2 className="font-condensed text-sm tracking-[0.3em] text-gray-400 mb-4">
          SUBSCRIBER SYNC
        </h2>

        <div className="space-y-3 font-mono text-xs">
          <Row label="LAST SYNC">
            <span className={settings?.sync_overdue ? 'text-amber-500' : 'text-gray-300'}>
              {fmtTime(settings?.last_sync_timestamp)}
              {settings?.sync_overdue && (
                <span className="ml-3 font-condensed tracking-widest text-amber-500">
                  ⚠ OVERDUE
                </span>
              )}
            </span>
          </Row>
          <Row label="SUBSCRIBERS">
            <span className="text-gray-300">{settings?.total_subscribers ?? '—'} total</span>
          </Row>
        </div>

        {/* Upload */}
        <div className="mt-5">
          <input
            ref={fileRef}
            type="file"
            accept=".csv"
            onChange={handleFileChange}
            className="hidden"
          />
          <button
            onClick={() => fileRef.current?.click()}
            disabled={syncMutation.isPending}
            className="font-condensed text-xs tracking-widest border border-gray-700 px-4 py-2 text-gray-300 hover:border-gray-500 hover:text-white disabled:opacity-40"
          >
            {syncMutation.isPending ? 'UPLOADING...' : 'UPLOAD SUBSCRIBER CSV'}
          </button>

          {/* Upload result */}
          {uploadResult && (
            <div className="mt-3 font-mono text-xs border border-gray-800 px-4 py-3 space-y-1">
              <p className="text-green-500 font-condensed tracking-widest text-[11px] mb-2">
                SYNC COMPLETE
              </p>
              <Row label="PROCESSED">{uploadResult.processed}</Row>
              <Row label="INSERTED">{uploadResult.inserted}</Row>
              <Row label="UPDATED">{uploadResult.updated}</Row>
              <Row label="MARKED DELETED">{uploadResult.marked_deleted}</Row>
              {uploadResult.errors?.length > 0 && (
                <div>
                  <p className="text-red-400 mt-2">{uploadResult.errors.length} row errors:</p>
                  <ul className="text-gray-600 mt-1 space-y-0.5">
                    {uploadResult.errors.slice(0, 5).map((e, i) => (
                      <li key={i}>· {e}</li>
                    ))}
                    {uploadResult.errors.length > 5 && (
                      <li className="text-gray-700">
                        +{uploadResult.errors.length - 5} more
                      </li>
                    )}
                  </ul>
                </div>
              )}
            </div>
          )}

          {uploadError && (
            <p className="font-mono text-xs text-red-500 mt-2">{uploadError}</p>
          )}
        </div>
      </section>

      <hr className="border-gray-800 mb-8" />

      {/* ── Section 2: Substack Connection ────────────────────────── */}
      <section className="mb-8">
        <h2 className="font-condensed text-sm tracking-[0.3em] text-gray-400 mb-4">
          SUBSTACK CONNECTION
        </h2>

        <div className="font-mono text-xs mb-5">
          <Row label="COOKIE STATUS">
            {settings?.cookie_expired ? (
              <span className="text-red-500 font-condensed tracking-widest">EXPIRED</span>
            ) : (
              <span className="text-green-500 font-condensed tracking-widest">OK</span>
            )}
          </Row>
        </div>

        {settings?.cookie_expired && (
          <div className="border border-red-500/20 px-5 py-4 space-y-3">
            <p className="font-condensed text-xs tracking-widest text-red-400">
              SESSION COOKIE EXPIRED — ACTION REQUIRED
            </p>
            <ol className="font-mono text-xs text-gray-400 space-y-2 list-decimal list-inside">
              <li>Open Chrome and navigate to your Substack publication dashboard.</li>
              <li>
                Open DevTools (F12) → Application tab → Cookies →{' '}
                <span className="text-gray-300">substack.com</span>
              </li>
              <li>
                Find the cookie named{' '}
                <span className="text-gray-200">substack.sid</span> and copy its value.
              </li>
              <li>
                Update the <span className="text-gray-200">SUBSTACK_SESSION_COOKIE</span>{' '}
                environment variable on Railway with the new value.
              </li>
              <li>
                Redeploy the backend service — Bridge will clear the expiry flag
                automatically on next successful navigation.
              </li>
            </ol>
          </div>
        )}
      </section>

      <hr className="border-gray-800 mb-8" />

      {/* ── Section 3: System ─────────────────────────────────────── */}
      <section>
        <h2 className="font-condensed text-sm tracking-[0.3em] text-gray-400 mb-4">
          SYSTEM
        </h2>

        <div className="space-y-3 font-mono text-xs">
          <Row label="ENVIRONMENT">
            <span
              className={
                settings?.environment === 'production' ? 'text-green-500' : 'text-amber-500'
              }
            >
              {settings?.environment?.toUpperCase() ?? '—'}
            </span>
          </Row>
          <Row label="API KEY">
            <span className="text-gray-400">{maskKey(apiKey)}</span>
          </Row>
        </div>
      </section>
    </div>
  )
}

function Row({ label, children }) {
  return (
    <div className="flex gap-4 items-baseline">
      <span className="text-gray-600 w-32 shrink-0 font-condensed tracking-widest text-[11px]">
        {label}
      </span>
      <span>{children}</span>
    </div>
  )
}
