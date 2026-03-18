import { Link, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import api from '../lib/api'

const NAV_ITEMS = [
  { path: '/', label: 'INBOX' },
  { path: '/log', label: 'LOG' },
  { path: '/failed', label: 'FAILED' },
  { path: '/settings', label: 'SETTINGS' },
]

export default function TopBar() {
  const location = useLocation()

  const { data: summary } = useQuery({
    queryKey: ['summary'],
    queryFn: () => api.get('/dashboard/summary').then((r) => r.data),
    refetchInterval: 30_000,
  })

  const pending = summary?.pending_review ?? 0
  const failed = summary?.failed_actions ?? 0
  const syncOverdue = summary?.sync_overdue ?? false
  const cookieExpired = summary?.cookie_expired ?? false

  return (
    <div className="border-b border-gray-800 px-8 h-12 flex items-center gap-8">
      {/* Wordmark */}
      <span className="font-condensed text-white font-semibold tracking-[0.35em] text-base shrink-0">
        BRIDGE
      </span>

      {/* Navigation */}
      <nav className="flex gap-6 flex-1">
        {NAV_ITEMS.map(({ path, label }) => {
          const active = location.pathname === path
          return (
            <Link
              key={path}
              to={path}
              className={[
                'font-condensed text-sm tracking-widest pb-0.5',
                active
                  ? 'text-white border-b border-amber-500'
                  : 'text-gray-500 hover:text-gray-300',
              ].join(' ')}
            >
              {label}
            </Link>
          )
        })}
      </nav>

      {/* Status indicators */}
      <div className="flex items-center gap-6 font-mono text-[11px] shrink-0">
        <span className={pending > 0 ? 'text-amber-500' : 'text-gray-600'}>
          PENDING: {summary ? pending : '—'}
        </span>

        <span className={failed > 0 ? 'text-red-500' : 'text-gray-600'}>
          FAILED: {summary ? failed : '—'}
        </span>

        <span className={syncOverdue ? 'text-amber-500' : 'text-gray-600'}>
          SYNC: {summary ? (syncOverdue ? 'OVERDUE' : 'CURRENT') : '—'}
        </span>

        {/* Cookie status with tooltip */}
        <div className="relative group">
          <span className={cookieExpired ? 'text-red-500 cursor-help' : 'text-gray-600'}>
            COOKIE: {summary ? (cookieExpired ? 'EXPIRED' : 'OK') : '—'}
          </span>
          {cookieExpired && (
            <div className="absolute right-0 top-5 w-64 bg-gray-900 border border-gray-700 px-3 py-2 text-gray-300 text-[10px] leading-relaxed hidden group-hover:block z-50 pointer-events-none">
              Substack session cookie has expired. See Settings.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
