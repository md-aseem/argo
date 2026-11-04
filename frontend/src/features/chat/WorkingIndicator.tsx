import styles from './WorkingIndicator.module.css'

interface Props {
  label: string
  /** working: a live run. stopped: you cancelled it. error: the run failed. */
  tone?: 'working' | 'stopped' | 'error'
  /** Nothing above it but (at most) the tool summary, so skip the extra breathing room. */
  flush?: boolean
}

/** The status line under a reply, led by a small square (trail-sign yellow while working). */
export function WorkingIndicator({ label, tone = 'working', flush = false }: Props) {
  return (
    <div className={styles.row} data-tone={tone} data-flush={flush} role={tone === 'error' ? 'alert' : 'status'}>
      <span className={styles.marker} aria-hidden="true" />
      <span className={styles.label}>{label}</span>
    </div>
  )
}
