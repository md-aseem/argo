import { useEffect, useMemo, useRef, useState } from 'react'
import styles from './HtmlGraphic.module.css'

/** Keep generated scripts in an opaque origin, with no network or parent access. */
function previewDocument(html: string): string {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  const policy = doc.createElement('meta')
  policy.httpEquiv = 'Content-Security-Policy'
  policy.content = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'"
  doc.head.prepend(policy)

  const sizing = doc.createElement('style')
  sizing.textContent = `html, body { height: auto !important; min-height: 0 !important; }
    body { display: flow-root; }`
  doc.head.append(sizing)

  const observer = doc.createElement('script')
  observer.textContent = `(() => {
    let scheduled = false;
    let previous = 0;
    const measure = () => {
      scheduled = false;
      const body = document.body;
      const style = getComputedStyle(body);
      const height = Math.ceil(body.getBoundingClientRect().height
        + parseFloat(style.marginTop) + parseFloat(style.marginBottom));
      if (height !== previous) {
        previous = height;
        parent.postMessage({ type: 'argo:graphic-size', height }, '*');
      }
    };
    const schedule = () => {
      if (!scheduled) { scheduled = true; requestAnimationFrame(measure); }
    };
    new ResizeObserver(schedule).observe(document.body);
    new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true, attributes: true, characterData: true });
    addEventListener('load', schedule);
    document.fonts.ready.then(schedule);
    schedule();
  })();`
  doc.body.append(observer)
  return '<!doctype html>\n' + doc.documentElement.outerHTML
}

export function HtmlGraphic({ html }: { html: string }) {
  const frame = useRef<HTMLIFrameElement>(null)
  const document = useMemo(() => previewDocument(html), [html])
  const [height, setHeight] = useState(160)

  useEffect(() => {
    const resize = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow) return
      const data = event.data
      if (data?.type === 'argo:graphic-size' && typeof data.height === 'number'
        && Number.isFinite(data.height) && data.height > 0) {
        setHeight(Math.ceil(data.height))
      }
    }
    window.addEventListener('message', resize)
    return () => window.removeEventListener('message', resize)
  }, [])

  return (
    <div className={styles.graphic}>
      <iframe
        ref={frame}
        className={styles.frame}
        title="HTML graphic preview"
        sandbox="allow-scripts"
        referrerPolicy="no-referrer"
        srcDoc={document}
        style={{ height }}
      />
    </div>
  )
}
