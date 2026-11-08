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

/** Pretty-print a payload; plain strings are shown as-is so newlines read naturally. */
const pretty = (value: unknown) => (typeof value === 'string' ? value : JSON.stringify(value, null, 2))

/** load_artifacts output carries `contents: {file: text}`; split it out to show as raw text. */
function splitContents(response: unknown): { rest: unknown; files: [string, string][] } {
  if (response && typeof response === 'object' && !Array.isArray(response) && 'contents' in response) {
    const { contents, ...rest } = response as { contents: Record<string, string> }
    return { rest, files: Object.entries(contents ?? {}) }
  }
  return { rest: response, files: [] }
}

/** One tool call: a one-line preview that expands into the full input and output. */
function ToolCallRow({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false)
  return (
    <li className={styles.callItem}>
      <button type="button" className={styles.call} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className={styles.callName}>{call.name}</span>
        <span className={styles.callArgs}>{preview(call.args)}</span>
        <span className={styles.callState}>{call.done ? 'done' : '…'}</span>
      </button>
      {open && (
        <div className={styles.callDetail}>
          <div className={styles.callLabel}>Input</div>
          <pre className={styles.callBody}>{pretty(call.args)}</pre>
          <div className={styles.callLabel}>Output</div>
          {!call.done ? (
            <pre className={styles.callBody}>Running…</pre>
          ) : call.response === undefined || call.response === null ? (
            <pre className={styles.callBody}>(none)</pre>
          ) : (
            (() => {
              const { rest, files } = splitContents(call.response)
              return (
                <>
                  <pre className={styles.callBody}>{pretty(rest)}</pre>
                  {files.map(([name, text]) => (
                    <div key={name} className={styles.callDetail}>
                      <div className={styles.callLabel}>{name}</div>
                      <pre className={styles.callBody}>{text}</pre>
                    </div>
                  ))}
                </>
              )
            })()
          )}
        </div>
      )}
    </li>
  )
}

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
          {tools.map((t, i) => (
            <ToolCallRow key={`${t.id}-${i}`} call={t} />
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
