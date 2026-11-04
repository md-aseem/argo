import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchSessions } from '../chat/api'
import type { SessionSummary } from '../chat/types'

/** The conversation list. Call `refresh` whenever it may have changed. */
export function useSessions() {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const inflight = useRef<AbortController | null>(null)

  const refresh = useCallback(() => {
    inflight.current?.abort() // only the newest answer counts
    const ctl = new AbortController()
    inflight.current = ctl
    fetchSessions(ctl.signal)
      .then(setSessions)
      .catch(() => {
        /* keep the last list we had; the next refresh will try again */
      })
  }, [])

  useEffect(() => () => inflight.current?.abort(), [])

  return { sessions, refresh }
}
