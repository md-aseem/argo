import { artifactUrl } from '../chat/api'
import type { Artifact } from './types'
import styles from './ArtifactRow.module.css'

const lineCount = (n: number) => `+${n} ${n === 1 ? 'line' : 'lines'}`

/** A file Argo saved: path, size, and a VIEW link to the raw file. */
export function ArtifactRow({ sessionId, artifact }: { sessionId: string; artifact: Artifact }) {
  return (
    <a
      className={styles.row}
      href={artifactUrl(sessionId, artifact.name, artifact.version)}
      target="_blank"
      rel="noopener noreferrer"
    >
      <span className={styles.name}>
        {artifact.name}
        {artifact.lines !== null && <span className={styles.faint}> · {lineCount(artifact.lines)}</span>}
      </span>
      <span className={styles.view}>VIEW</span>
    </a>
  )
}
