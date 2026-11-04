import { memo, useState } from 'react'
import { ArtifactRow } from '../artifacts/ArtifactRow'
import { Markdown } from './Markdown'
import { WorkingIndicator } from './WorkingIndicator'
import type { ArgoMessage, ToolCall } from './types'
import styles from './ArgoMessage.module.css'

/** "read_file · write_file ×2": each tool once, with a count when repeated. */
function summarizeTools(tools: ToolCall[]): string {
  const counts = new Map<string, number>()
  for (const t of tools) counts.set(t.name, (counts.get(t.name) ?? 0) + 1)
  return [...counts].map(([name, n]) => (n > 1 ? `${name} ×${n}` : name)).join(' · ')
}

const preview = (args: Record<string, unknown>) => (Object.keys(args).length ? JSON.stringify(args) : '')

interface Props {
  message: ArgoMessage
  /** Needed to link artifacts; null until the server has created the session. */
  sessionId: string | null
}

/** One reply: the tools it used (if any), prose, saved files, then status. */
export const ArgoMessageView = memo(function ArgoMessageView({ message, sessionId }: Props) {
  const [open, setOpen] = useState(false)
  const { tools, artifacts } = message
  const bare = !message.text && artifacts.length === 0 // at most the tool summary above the status row

  return (
    <article className={styles.argo} aria-label="Argo">
      {tools.length > 0 && (
        <button
          type="button"
          className={styles.summary}
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          {summarizeTools(tools)} <span className={styles.caret}>›</span>
        </button>
      )}

      {open && (
        <ul className={styles.calls}>
          {tools.map((t) => (
            <li key={t.id} className={styles.call}>
              <span className={styles.callName}>{t.name}</span>
              <span className={styles.callArgs}>{preview(t.args)}</span>
              <span className={styles.callState}>{t.done ? 'done' : '…'}</span>
            </li>
          ))}
        </ul>
      )}

      {message.text && <Markdown>{message.text}</Markdown>}

      {sessionId && artifacts.length > 0 && (
        <div className={styles.artifacts}>
          {artifacts.map((a) => (
            <ArtifactRow key={`${a.name}@${a.version}`} sessionId={sessionId} artifact={a} />
          ))}
        </div>
      )}

      {message.status === 'streaming' && <WorkingIndicator label={message.activity} flush={bare} />}
      {message.status === 'stopped' && <WorkingIndicator tone="stopped" label="Stopped" flush={bare} />}
      {message.error && <WorkingIndicator tone="error" label={message.error} flush={bare} />}
    </article>
  )
})
