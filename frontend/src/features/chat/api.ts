import type { ApiSession, ChatRequest, SessionSummary, StreamEvent } from './types'

async function errorText(res: Response): Promise<string> {
  try {
    const body = await res.json()
    if (typeof body?.detail === 'string') return body.detail
  } catch {
    /* not JSON */
  }
  return `Request failed (${res.status})`
}

/** All conversations, most recently updated first. */
export async function fetchSessions(signal: AbortSignal): Promise<SessionSummary[]> {
  const res = await fetch('/api/sessions', { signal })
  if (!res.ok) throw new Error(await errorText(res))
  return res.json()
}

export async function fetchSession(id: string, signal: AbortSignal): Promise<ApiSession> {
  const res = await fetch(`/api/sessions/${encodeURIComponent(id)}`, { signal })
  if (!res.ok) throw new Error(await errorText(res))
  return res.json()
}

export async function deleteSession(id: string): Promise<void> {
  const res = await fetch(`/api/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(await errorText(res))
}

function parseFrame(frame: string): StreamEvent | null {
  let type = 'message'
  const data: string[] = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) type = line.slice(6).trim()
    else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
  }
  if (!data.length) return null
  return { type, data: JSON.parse(data.join('\n')) } as StreamEvent
}

/** POST a message and yield the reply as it streams in (server-sent events). */
export async function* streamChat(
  body: ChatRequest,
  signal: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) throw new Error(await errorText(res))

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    for (let end = buffer.indexOf('\n\n'); end !== -1; end = buffer.indexOf('\n\n')) {
      const event = parseFrame(buffer.slice(0, end))
      buffer = buffer.slice(end + 2)
      if (event) yield event
    }
  }
}

export function artifactUrl(sessionId: string, name: string, version: number): string {
  const path = name.split('/').map(encodeURIComponent).join('/')
  return `/api/sessions/${encodeURIComponent(sessionId)}/artifacts/${path}?version=${version}`
}
