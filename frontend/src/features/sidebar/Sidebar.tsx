import { useMemo, useRef, useState } from 'react'
import { HutLogo, PanelIcon, PlusIcon, TrashIcon } from '../../components/Icons'
import type { SessionSummary } from '../chat/types'
import { groupByDate } from './groupByDate'
import styles from './Sidebar.module.css'

interface Props {
  sessions: SessionSummary[]
  currentId: string | null
  /** Docked shows the conversation list; collapsed is a slim rail. */
  collapsed: boolean
  onToggle: () => void
  onNewChat: () => void
  onSelect: (id: string) => void
  onDelete: (id: string) => Promise<void>
  userName: string
  userInitials: string
}

export function Sidebar({
  sessions,
  currentId,
  collapsed,
  onToggle,
  onNewChat,
  onSelect,
  onDelete,
  userName,
  userInitials,
}: Props) {
  const groups = useMemo(() => groupByDate(sessions), [sessions])
  const toggleLabel = collapsed ? 'Expand sidebar' : 'Collapse sidebar'
  const pending = useRef(new Set<string>())
  const [deleting, setDeleting] = useState(new Set<string>())
  const [error, setError] = useState<string | null>(null)

  async function deleteChat(id: string) {
    if (pending.current.has(id)) return
    pending.current.add(id)
    setDeleting(new Set(pending.current))
    setError(null)
    try {
      await onDelete(id)
    } catch (err) {
      setError(`Could not delete chat: ${err instanceof Error ? err.message : 'Please try again.'}`)
    } finally {
      pending.current.delete(id)
      setDeleting(new Set(pending.current))
    }
  }

  return (
    <aside className={styles.sidebar} data-collapsed={collapsed} aria-label="Conversations">
      <div className={styles.header}>
        <HutLogo />
        <button
          type="button"
          className={styles.toggle}
          onClick={onToggle}
          aria-label={toggleLabel}
          aria-expanded={!collapsed}
          title={toggleLabel}
        >
          <PanelIcon />
        </button>
      </div>

      <button type="button" className={styles.newChat} onClick={onNewChat} aria-label="New chat" title="New chat">
        {!collapsed && <span>New chat</span>}
        <PlusIcon size={14} />
      </button>

      <nav className={styles.list}>
        {!collapsed &&
          groups.map((group) => (
            <section key={group.label} className={styles.group}>
              <h2 className={styles.label}>{group.label}</h2>
              <ul className={styles.rows}>
                {group.sessions.map((s) => (
                  <li key={s.id} className={styles.item} data-current={s.id === currentId}>
                    <button
                      type="button"
                      className={styles.row}
                      onClick={() => onSelect(s.id)}
                      aria-current={s.id === currentId ? 'page' : undefined}
                      title={s.title}
                      disabled={deleting.has(s.id)}
                    >
                      {s.title}
                    </button>
                    <button
                      type="button"
                      className={styles.delete}
                      onClick={() => void deleteChat(s.id)}
                      disabled={deleting.has(s.id)}
                      aria-label={`Delete chat: ${s.title}`}
                      title={deleting.has(s.id) ? 'Deleting…' : 'Delete chat'}
                    >
                      <TrashIcon />
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))}
      </nav>

      {error && <p className={styles.error} role="alert">{error}</p>}

      <div className={styles.user}>
        <div className={styles.avatar} aria-hidden="true">
          {userInitials}
        </div>
        {collapsed ? <span className={styles.srOnly}>{userName}</span> : <span className={styles.name}>{userName}</span>}
      </div>
    </aside>
  )
}
