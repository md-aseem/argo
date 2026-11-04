import { memo } from 'react'
import { FileIcon } from '../../components/Icons'
import type { UserMessage } from './types'
import styles from './UserMessage.module.css'

/** What you said: slate, right-aligned, with any files you attached below it. */
export const UserMessageView = memo(function UserMessageView({ message }: { message: UserMessage }) {
  return (
    <article className={styles.user} aria-label="You">
      {message.text && <p className={styles.text}>{message.text}</p>}
      {message.attachments.length > 0 && (
        <ul className={styles.files}>
          {message.attachments.map((file, i) => (
            <li key={`${file.name}-${i}`} className={styles.file}>
              <FileIcon />
              <span>{file.name}</span>
            </li>
          ))}
        </ul>
      )}
    </article>
  )
})
