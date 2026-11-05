import { isValidElement, memo, type ComponentProps } from 'react'
import { HtmlGraphic } from '../artifacts/HtmlGraphic'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import styles from './Markdown.module.css'

function ExternalLink({ node: _node, ...props }: ComponentProps<'a'> & { node?: unknown }) {
  return <a {...props} target="_blank" rel="noopener noreferrer" />
}

function CodeBlock({ node: _node, children, ...props }: ComponentProps<'pre'> & { node?: unknown }) {
  if (isValidElement<{ className?: string; children?: unknown }>(children)
    && /\blanguage-html\b/.test(children.props.className ?? '')
    && typeof children.props.children === 'string') {
    return <HtmlGraphic html={children.props.children} />
  }
  return <pre {...props}>{children}</pre>
}

/** Renders Argo's reply. Raw HTML in the text is not rendered. */
export const Markdown = memo(function Markdown({ children }: { children: string }) {
  return (
    <div className={styles.prose}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: ExternalLink, pre: CodeBlock }}>
        {children}
      </ReactMarkdown>
    </div>
  )
})
