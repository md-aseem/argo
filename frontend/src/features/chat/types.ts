import type { Artifact } from '../artifacts/types'

export interface SessionInfo {
  id: string
  number: number | null
  title: string
}

export interface ToolCall {
  id: string
  name: string
  args: Record<string, unknown>
  done: boolean
}

export interface UserMessage {
  id: string
  role: 'user'
  text: string
  attachments: { name: string }[]
}

export interface ArgoMessage {
  id: string
  role: 'argo'
  text: string
  tools: ToolCall[]
  artifacts: Artifact[]
  error: string | null
  /** streaming: the run is live. stopped: the user cancelled it. */
  status: 'streaming' | 'done' | 'stopped'
  /** What the working indicator says while streaming. */
  activity: string
  /** A tool call came after the text so far, so the next text starts a new paragraph. */
  breakBefore: boolean
}

export type Message = UserMessage | ArgoMessage

// ── wire format (see src/main.py) ───────────────────────────────────────────

/** One row of GET /api/sessions. `updated_at` is Unix time in seconds. */
export interface SessionSummary extends SessionInfo {
  updated_at: number
}

export interface ApiArgoMessage {
  id: string
  role: 'argo'
  text: string
  tools: { id: string; name: string; args: Record<string, unknown> }[]
  artifacts: Artifact[]
  error: string | null
}

export interface ApiSession extends SessionInfo {
  messages: (UserMessage | ApiArgoMessage)[]
}

export interface ChatRequest {
  session_id: string | null
  text: string
  attachments: { name: string; mime_type: string; data: string }[]
}

export type StreamEvent =
  | { type: 'session'; data: SessionInfo }
  | { type: 'text'; data: { delta: string } }
  | { type: 'tool'; data: { id: string; name: string; args: Record<string, unknown> } }
  | { type: 'tool_result'; data: { id: string; name: string } }
  | { type: 'artifact'; data: Artifact }
  | { type: 'error'; data: { message: string } }
  | { type: 'done'; data: Record<string, never> }
