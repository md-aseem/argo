import type { SessionSummary } from '../chat/types'

export interface SessionGroup {
  label: string
  sessions: SessionSummary[]
}

const DAY_MS = 24 * 60 * 60 * 1000

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
}

/**
 * Bucket sessions (already sorted newest first) into Today, Yesterday,
 * Last 7 days, then one group per month ("October", or "October 2025" for an
 * earlier year).
 */
export function groupByDate(sessions: SessionSummary[], now = new Date()): SessionGroup[] {
  const today = startOfDay(now)
  const yesterday = today - DAY_MS
  const weekAgo = today - 7 * DAY_MS

  const groups: SessionGroup[] = []
  const labelFor = (ms: number): string => {
    if (ms >= today) return 'Today'
    if (ms >= yesterday) return 'Yesterday'
    if (ms >= weekAgo) return 'Last 7 days'
    const d = new Date(ms)
    const month = d.toLocaleString(undefined, { month: 'long' })
    return d.getFullYear() === now.getFullYear() ? month : `${month} ${d.getFullYear()}`
  }

  for (const s of sessions) {
    const label = labelFor(s.updated_at * 1000)
    const last = groups[groups.length - 1]
    if (last?.label === label) last.sessions.push(s)
    else groups.push({ label, sessions: [s] })
  }
  return groups
}
