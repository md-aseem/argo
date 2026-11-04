import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '../../components/TopBar'
import { Sidebar } from '../sidebar/Sidebar'
import { useSessions } from '../sidebar/useSessions'
import { Composer } from './Composer'
import { Thread } from './Thread'
import { useChat } from './useChat'
import styles from './ChatView.module.css'

const USER_NAME = 'Aseem'
const USER_INITIALS = 'AS'

const COLLAPSED_KEY = 'argo.sidebar.collapsed'
const NARROW = '(max-width: 720px)'

/** Docked unless the viewer collapsed it before, or the screen is narrow. */
function initialCollapsed(): boolean {
  try {
    const saved = localStorage.getItem(COLLAPSED_KEY)
    if (saved !== null) return saved === 'true'
  } catch {
    /* storage blocked: fall through to the default */
  }
  return window.matchMedia(NARROW).matches
}

export function ChatView() {
  const { session, messages, loading, busy, send, stop, reset, switchTo } = useChat()
  const { sessions, refresh } = useSessions()
  const [collapsed, setCollapsed] = useState(initialCollapsed)

  useEffect(() => {
    document.title = session ? `${session.title} · Argo` : 'Argo'
  }, [session])

  // A new conversation appears as soon as it exists, and a finished reply moves
  // its conversation to the top.
  useEffect(() => {
    refresh()
  }, [session?.id, busy, refresh])

  const toggle = useCallback(() => {
    setCollapsed((was) => {
      const next = !was
      try {
        localStorage.setItem(COLLAPSED_KEY, String(next))
      } catch {
        /* not remembered this time; still toggles */
      }
      return next
    })
  }, [])

  return (
    <div className={styles.view}>
      <Sidebar
        sessions={sessions}
        currentId={session?.id ?? null}
        collapsed={collapsed}
        onToggle={toggle}
        onNewChat={reset}
        onSelect={switchTo}
        userName={USER_NAME}
        userInitials={USER_INITIALS}
      />
      <main className={styles.main}>
        <TopBar title={loading ? '' : (session?.title ?? 'New chat')} />
        <Thread messages={messages} sessionId={session?.id ?? null} loading={loading} />
        <Composer busy={busy} onSend={send} onStop={stop} />
      </main>
    </div>
  )
}
