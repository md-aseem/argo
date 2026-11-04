import styles from './TopBar.module.css'

interface Props {
  title: string
}

/** The open conversation's title, centred over the chat column. */
export function TopBar({ title }: Props) {
  return (
    <header className={styles.bar}>
      <h1 className={styles.title}>{title}</h1>
    </header>
  )
}
