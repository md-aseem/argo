import { useLayoutEffect, useRef, useState, type ChangeEvent, type FormEvent, type KeyboardEvent } from 'react'
import { ArrowUpIcon, CloseIcon, FileChipIcon, MicIcon, PlusIcon, StopIcon } from '../../components/Icons'
import { useSpeechInput } from './useSpeechInput'
import styles from './Composer.module.css'

const TAGLINE = 'Check anything that matters.'
const MAX_TOTAL_BYTES = 15 * 1024 * 1024 // matches the server limit
const MAX_INPUT_HEIGHT = 200

interface Props {
  busy: boolean
  onSend: (text: string, files: File[]) => void
  onStop: () => void
}

export function Composer({ busy, onSend, onStop }: Props) {
  const [text, setText] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const picker = useRef<HTMLInputElement>(null)

  const speech = useSpeechInput((phrase) => setText((t) => (t && !/\s$/.test(t) ? `${t} ${phrase}` : t + phrase)))

  // Grow with the text up to a cap, then scroll.
  useLayoutEffect(() => {
    const el = input.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, MAX_INPUT_HEIGHT)}px`
  }, [text])

  const canSend = !busy && (text.trim() !== '' || files.length > 0)

  const submit = () => {
    if (!canSend) return
    speech.stop()
    onSend(text.trim(), files)
    setText('')
    setFiles([])
    setNotice(null)
    input.current?.focus()
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (busy) onStop()
    else submit()
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  const onPick = (e: ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files ?? [])
    e.target.value = '' // lets the same file be picked again later
    if (!picked.length) return
    const next = [...files, ...picked]
    if (next.reduce((sum, f) => sum + f.size, 0) > MAX_TOTAL_BYTES) {
      setNotice('Attachments are limited to 15 MB in total.')
      return
    }
    setNotice(null)
    setFiles(next)
  }

  return (
    <div className={styles.dock}>
      <form className={styles.box} onSubmit={onSubmit}>
        {files.length > 0 && (
          <ul className={styles.chips}>
            {files.map((f, i) => (
              <li key={`${f.name}-${i}`} className={styles.chip}>
                <FileChipIcon />
                <span className={styles.chipName}>{f.name}</span>
                <button
                  type="button"
                  className={styles.chipRemove}
                  aria-label={`Remove ${f.name}`}
                  onClick={() => setFiles((all) => all.filter((_, j) => j !== i))}
                >
                  <CloseIcon />
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className={styles.row}>
          <button type="button" className={styles.tool} aria-label="Attach files" onClick={() => picker.current?.click()}>
            <PlusIcon />
          </button>
          <input ref={picker} type="file" multiple hidden onChange={onPick} />

          <textarea
            ref={input}
            className={styles.input}
            rows={1}
            value={text}
            autoFocus
            placeholder="Ask Argo, or type / for commands"
            aria-label="Message Argo"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
          />

          {speech.supported && (
            <button
              type="button"
              className={styles.tool}
              data-active={speech.listening}
              aria-label={speech.listening ? 'Stop dictation' : 'Dictate'}
              aria-pressed={speech.listening}
              onClick={speech.toggle}
            >
              <MicIcon />
            </button>
          )}

          <button
            type="submit"
            className={styles.send}
            disabled={!busy && !canSend}
            aria-label={busy ? 'Stop' : 'Send'}
          >
            {busy ? <StopIcon /> : <ArrowUpIcon />}
          </button>
        </div>
      </form>

      <p className={styles.note} data-notice={notice !== null} role={notice ? 'alert' : undefined}>
        {notice ?? TAGLINE}
      </p>
    </div>
  )
}
