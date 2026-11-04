import { useLayoutEffect, useRef } from 'react'
import { ArgoMessageView } from './ArgoMessage'
import { UserMessageView } from './UserMessage'
import type { Message } from './types'
import styles from './Thread.module.css'

interface Props {
  messages: Message[]
  sessionId: string | null
  /** Restoring a conversation: show nothing rather than flash the empty state. */
  loading: boolean
}

// Within this many px of the bottom counts as "following along".
const FOLLOW_SLACK = 96

export function Thread({ messages, sessionId, loading }: Props) {
  const scroller = useRef<HTMLDivElement>(null)
  const count = useRef(0)
  const lastHeight = useRef(0)

  // Keep the newest text in view while it streams, unless you scrolled up to read.
  // "Were we at the bottom?" is judged against the height before this update, so it
  // reflects exactly where you are now (a scroll event would lag a frame behind).
  useLayoutEffect(() => {
    const el = scroller.current
    if (!el) return
    const wasAtBottom = lastHeight.current - el.scrollTop - el.clientHeight < FOLLOW_SLACK
    const isNewMessage = messages.length > count.current // sending or restoring always jumps down
    count.current = messages.length
    if (wasAtBottom || isNewMessage) el.scrollTop = el.scrollHeight
    lastHeight.current = el.scrollHeight
  }, [messages])

  return (
    <div className={styles.scroller} ref={scroller}>
      <div className={styles.column} role="log" aria-label="Conversation">
        {!loading && messages.length === 0 && <EmptyState />}
        {messages.map((m) =>
          m.role === 'user' ? (
            <UserMessageView key={m.id} message={m} />
          ) : (
            <ArgoMessageView key={m.id} message={m} sessionId={sessionId} />
          ),
        )}
      </div>
    </div>
  )
}

function EmptyState() {
  return <p className={styles.greeting}>What are we working on?</p>
}
