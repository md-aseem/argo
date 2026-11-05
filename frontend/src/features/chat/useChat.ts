import { useCallback, useEffect, useRef, useState } from 'react'
import { deleteSession, fetchSession, streamChat } from './api'
import type {
  ApiArgoMessage,
  ArgoMessage,
  Message,
  SessionInfo,
  StreamEvent,
  UserMessage,
} from './types'

const THINKING = 'Thinking…'

const newId = () => crypto.randomUUID()

function newArgoMessage(id: string): ArgoMessage {
  return {
    id,
    role: 'argo',
    text: '',
    tools: [],
    artifacts: [],
    error: null,
    status: 'streaming',
    activity: THINKING,
    breakBefore: false,
  }
}

/** Fold one stream event into the Argo message it belongs to. */
function applyEvent(msg: ArgoMessage, event: StreamEvent): ArgoMessage {
  switch (event.type) {
    case 'text': {
      const lead = msg.breakBefore && msg.text ? '\n\n' : ''
      return { ...msg, text: msg.text + lead + event.data.delta, breakBefore: false, activity: 'Writing…' }
    }
    case 'tool':
      return {
        ...msg,
        tools: [...msg.tools, { ...event.data, done: false }],
        breakBefore: msg.text !== '',
        activity: `Running ${event.data.name}…`,
      }
    case 'tool_result':
      return {
        ...msg,
        tools: msg.tools.map((t) => (t.id === event.data.id ? { ...t, done: true } : t)),
        activity: THINKING,
      }
    case 'artifact':
      return { ...msg, artifacts: [...msg.artifacts, event.data] }
    case 'error':
      return { ...msg, status: 'done', error: event.data.message }
    case 'done':
      return { ...msg, status: 'done' }
    default:
      return msg
  }
}

function fromApi(m: UserMessage | ApiArgoMessage): Message {
  if (m.role === 'user') return m
  return {
    ...m,
    tools: m.tools.map((t) => ({ ...t, done: true })),
    status: 'done',
    activity: THINKING,
    breakBefore: false,
  }
}

function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '')
    reader.onerror = () => reject(reader.error ?? new Error(`Could not read ${file.name}`))
    reader.readAsDataURL(file)
  })
}

// The open conversation lives in the URL (?s=<id>) so a reload keeps it.
const sessionIdFromUrl = () => new URLSearchParams(window.location.search).get('s')

function setUrlSession(id: string | null) {
  const url = new URL(window.location.href)
  if (id) url.searchParams.set('s', id)
  else url.searchParams.delete('s')
  window.history.replaceState(null, '', url)
}

export function useChat() {
  const [session, setSession] = useState<SessionInfo | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(() => sessionIdFromUrl() !== null)
  const [busy, setBusy] = useState(false)

  const sessionId = useRef<string | null>(null)
  const running = useRef<AbortController | null>(null)
  const loadingCtl = useRef<AbortController | null>(null)

  /** Fetch a stored conversation and show it. A newer load cancels an older one. */
  const load = useCallback((id: string) => {
    loadingCtl.current?.abort()
    const ctl = new AbortController()
    loadingCtl.current = ctl
    setLoading(true)
    fetchSession(id, ctl.signal)
      .then((s) => {
        sessionId.current = s.id
        setSession({ id: s.id, number: s.number, title: s.title })
        setMessages(s.messages.map(fromApi))
      })
      .catch(() => {
        if (ctl.signal.aborted) return
        // Gone or unreadable: start fresh.
        sessionId.current = null
        setSession(null)
        setMessages([])
        setUrlSession(null)
      })
      .finally(() => {
        if (!ctl.signal.aborted) setLoading(false)
      })
  }, [])

  // Restore the conversation named in the URL.
  useEffect(() => {
    const id = sessionIdFromUrl()
    if (id) load(id)
    return () => loadingCtl.current?.abort()
  }, [load])

  /** Stop whatever is in flight: a streaming reply or a conversation load. */
  const cancelAll = useCallback(() => {
    running.current?.abort()
    running.current = null
    loadingCtl.current?.abort()
    loadingCtl.current = null
    setBusy(false)
    setLoading(false)
  }, [])

  const send = useCallback(async (text: string, files: File[]) => {
    const ctl = new AbortController()
    running.current = ctl
    const argoId = newId()
    const patch = (fn: (m: ArgoMessage) => ArgoMessage) =>
      setMessages((all) => all.map((m) => (m.id === argoId && m.role === 'argo' ? fn(m) : m)))

    setMessages((all) => [
      ...all,
      { id: newId(), role: 'user', text, attachments: files.map((f) => ({ name: f.name })) },
      newArgoMessage(argoId),
    ])
    setBusy(true)

    let ended = false
    try {
      const attachments = await Promise.all(
        files.map(async (f) => ({ name: f.name, mime_type: f.type, data: await toBase64(f) })),
      )
      for await (const event of streamChat(
        { session_id: sessionId.current, text, attachments },
        ctl.signal,
      )) {
        if (event.type === 'session') {
          sessionId.current = event.data.id
          setSession(event.data)
          setUrlSession(event.data.id)
          continue
        }
        if (event.type === 'done' || event.type === 'error') ended = true
        patch((m) => applyEvent(m, event))
      }
      if (!ended) patch((m) => ({ ...m, status: 'done', error: 'The connection closed before the reply finished.' }))
    } catch (err) {
      if (ctl.signal.aborted) patch((m) => ({ ...m, status: 'stopped' }))
      else patch((m) => ({ ...m, status: 'done', error: err instanceof Error ? err.message : 'Something went wrong' }))
    } finally {
      if (running.current === ctl) {
        running.current = null
        setBusy(false)
      }
    }
  }, [])

  const stop = useCallback(() => running.current?.abort(), [])

  const reset = useCallback(() => {
    cancelAll()
    sessionId.current = null
    setSession(null)
    setMessages([])
    setUrlSession(null)
  }, [cancelAll])

  /** Open a past conversation. Switching away stops a reply that is still streaming. */
  const switchTo = useCallback(
    (id: string) => {
      if (id === sessionId.current && !running.current) return
      cancelAll()
      sessionId.current = id
      setSession(null)
      setMessages([])
      setUrlSession(id)
      load(id)
    },
    [cancelAll, load],
  )

  const remove = useCallback(async (id: string) => {
    if (id === sessionId.current || id === sessionIdFromUrl()) cancelAll()
    await deleteSession(id)
    if (id === sessionId.current || id === sessionIdFromUrl()) reset()
  }, [cancelAll, reset])

  return { session, messages, loading, busy, send, stop, reset, switchTo, remove }
}
