---
name: illustrate
description: Create responsive HTML graphics that explain ideas visually inside Argo chat. Use for visual explanations, comparisons, processes, timelines, diagrams, and simple interactive illustrations when seeing the relationships makes the answer easier to understand.
---

# illustrate

Create an HTML-based graphic that makes the user's subject clear through layout, labels, and visual relationships. Choose the simplest useful form: a comparison, sequence, annotated diagram, timeline, chart, or small interactive example. Let the graphic carry the explanation; keep accompanying prose brief.

Before building, read [references/style-guide.md](references/style-guide.md) for Argo's colors, tokens, and basic components. Use those styles as the starting point and adapt the arrangement to the subject. Keep it simple.

## Fit the chat

- Fill the available chat container width. The current chat column is at most 640px, but the graphic must also work when its container is narrower. Use `width: 100%`, `max-width: 100%`, and border-box sizing instead of a fixed desktop canvas or `100vw`.
- Let the graphic be as long as it needs to be. Use natural document height; avoid fixed heights, viewport-height sections, clipped content, and internal vertical scrolling.
- Reflow columns into a vertical sequence at narrow widths. Prefer wrapping flex layouts, auto-fitting grids, or container queries so layout follows the embedded container rather than the browser window.
- Keep labels readable without zooming. Wrap long text, give grid/flex children `min-width: 0`, and keep SVGs and images within their container. Avoid scaling an entire wide diagram down until its labels become tiny.
- Use local horizontal scrolling only for a table or code block that genuinely needs it. The graphic itself should not cause horizontal scrolling.

## Build the graphic

- Deliver a self-contained HTML document with a viewport meta tag and inline CSS. Include any small JavaScript inline only when interaction helps understanding. Avoid external libraries, font downloads, and remote assets for a basic graphic.
- Use semantic HTML and CSS for structure; use inline SVG where connectors or plotted shapes improve the explanation. Include visible labels and a short text equivalent for a diagram whose meaning is otherwise only visual.
- Keep the visual hierarchy quiet.

## Display in chat

Return the complete HTML document in a fenced code block labeled `html`. The UI automatically renders that block as a graphic, fitting it to the chat width and adjusting its height to the content. Use inline CSS, inline JavaScript, inline SVG, and data URLs; the preview blocks external network requests. Add a brief caption only if useful. No file-saving tool is needed.
