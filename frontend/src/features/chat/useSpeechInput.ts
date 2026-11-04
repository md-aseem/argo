import { useCallback, useEffect, useRef, useState } from 'react'

// The Web Speech API isn't in lib.dom yet; this is the slice we use.
interface Recognition {
  continuous: boolean
  interimResults: boolean
  lang: string
  start(): void
  stop(): void
  onresult: ((e: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null
  onend: (() => void) | null
  onerror: (() => void) | null
}

type RecognitionCtor = new () => Recognition

const Ctor = (
  window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor }
)
const Recognizer = Ctor.SpeechRecognition ?? Ctor.webkitSpeechRecognition

/**
 * Dictation via the browser's built-in speech recognition (Chrome and Safari;
 * Chrome processes the audio on Google's servers). Finished phrases go to `onText`.
 */
export function useSpeechInput(onText: (text: string) => void) {
  const [listening, setListening] = useState(false)
  const recognition = useRef<Recognition | null>(null)
  const latest = useRef(onText)
  latest.current = onText

  const stop = useCallback(() => recognition.current?.stop(), [])

  const start = useCallback(() => {
    if (!Recognizer || recognition.current) return
    const r = new Recognizer()
    r.continuous = true
    r.interimResults = false
    r.lang = navigator.language
    r.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const result = e.results[i]
        if (result.isFinal) latest.current(result[0].transcript.trim())
      }
    }
    const done = () => {
      recognition.current = null
      setListening(false)
    }
    r.onend = done
    r.onerror = done
    recognition.current = r
    setListening(true)
    r.start()
  }, [])

  useEffect(() => () => recognition.current?.stop(), [])

  return {
    supported: Recognizer !== undefined,
    listening,
    toggle: () => (recognition.current ? stop() : start()),
    stop,
  }
}
