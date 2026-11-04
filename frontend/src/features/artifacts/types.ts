/** A file Argo saved during a reply (ADK artifact). `lines` is null for binary files. */
export interface Artifact {
  name: string
  version: number
  lines: number | null
  mime: string | null
}
