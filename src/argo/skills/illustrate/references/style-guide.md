# Argo HTML graphics: basic style guide

Based on `frontend/src/app/tokens.css`, `global.css`, and the chat's `Markdown.module.css`. The palette and typography below come from Argo. The spacing scale and component classes are lightweight defaults for generated graphics, not additional application-wide tokens.

## Colors and typography

| Token | Value | Use |
| --- | --- | --- |
| `--color-ink` | `#111111` | Main text, strong rules, focus |
| `--color-slate` | `#5e6a70` | Supporting text |
| `--color-tape-pink` | `#ffb3e6` | Occasional highlighted group |
| `--color-trail-yellow` | `#edf24a` | Key takeaway or highlighted value |
| `--color-blaze` | `#ff5b14` | Small markers or emphasis |
| `--color-white` | `#ffffff` | Main surface |
| `--color-slate-muted` | `#6b7378` | Metadata |
| `--color-slate-faint` | `#9aa3a7` | Decorative, nonessential marks |
| `--color-hairline` | `#e2e6e6` | Dividers and borders |
| `--color-chip` | `#eef1f1` | Chips, code, subtle fills |
| `--color-snow` | `#fafbfb` | Secondary surface |

Use Inter Tight for ordinary text and JetBrains Mono for code, numeric readouts, or small technical labels. The system fallbacks should look good when those fonts are unavailable; a standalone document does not inherit the app's loaded fonts.

Body copy is 16px/24px, emphasis is weight 600, primary headings are 1.4em, and secondary headings are 1.2em. Use 12–14px sparingly for short secondary labels. Use ink text on accent fills; blaze and faint slate are not colors for small essential text on white.

## Basic visual rules

- White background, ink text, generous whitespace, thin neutral dividers.
- Use one accent for the main point; add others only when they encode distinct groups. Pair color with labels.
- Prefer flat sections and square corners. Skip shadows, gradients, ornamental backgrounds, and excessive cards.
- Use a simple spacing scale: 4, 8, 12, 16, 24, and 32px. Internal padding is usually 12–16px; larger sections are 24px apart.
- Keep prose left aligned. Use mono for code and occasional data labels, not entire paragraphs.
- Build only the components the explanation needs. A row, a labeled group, and a divider are often enough.

## Copyable base CSS and components

Include this in the document's `<style>` element and remove unused classes if useful. The wrapper is a query container: its child grid responds to the graphic's width, including when embedded in a wide browser window.

```css
:root {
  --color-ink: #111111;
  --color-slate: #5e6a70;
  --color-tape-pink: #ffb3e6;
  --color-trail-yellow: #edf24a;
  --color-blaze: #ff5b14;
  --color-white: #ffffff;
  --color-slate-muted: #6b7378;
  --color-slate-faint: #9aa3a7;
  --color-hairline: #e2e6e6;
  --color-chip: #eef1f1;
  --color-snow: #fafbfb;
  --font-display: 'Inter Tight Variable', 'Inter Tight', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono Variable', 'JetBrains Mono', ui-monospace, monospace;
  --text-body: 16px;
  --leading-body: 24px;
  /* Local graphic defaults. */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-6: 24px;
  --space-8: 32px;
}
*, *::before, *::after { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--color-white);
  color: var(--color-ink);
  font: var(--text-body)/var(--leading-body) var(--font-display);
  color-scheme: light;
}
.graphic {
  container-type: inline-size;
  width: 100%;
  max-width: 100%;
  padding: var(--space-4);
  overflow-wrap: anywhere;
}
h1, h2, p, figure { margin: 0; }
h1, h2 { font-weight: 600; letter-spacing: -0.015em; line-height: 1.3; }
h1 { font-size: 1.4em; }
h2 { font-size: 1.2em; }
strong { font-weight: 600; }
.stack { display: grid; gap: var(--space-4); }
.section { margin-top: var(--space-6); }
.row { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--space-2) var(--space-4); }
.grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--space-4); }
.grid > *, .row > * { min-width: 0; }
@container (min-width: 480px) {
  .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
.panel { padding: var(--space-4); border: 1px solid var(--color-hairline); }
.muted { color: var(--color-slate); }
.label { color: var(--color-slate-muted); font-size: 0.875em; }
.value { font-size: 1.4em; font-weight: 600; line-height: 1.3; }
.chip { display: inline-block; padding: 2px 8px; background: var(--color-chip); font-size: 0.875em; }
.highlight { background: var(--color-trail-yellow); color: var(--color-ink); padding: 0 4px; }
.callout { border-left: 2px solid var(--color-ink); padding-left: var(--space-4); }
hr { border: 0; border-top: 1px solid var(--color-hairline); margin: var(--space-6) 0; }
a { color: inherit; text-underline-offset: 4px; }
a:hover { background: var(--color-trail-yellow); }
button, input, select { font: inherit; color: inherit; max-width: 100%; }
button { padding: 8px 12px; border: 1px solid var(--color-hairline); background: var(--color-white); cursor: pointer; }
button:hover { background: var(--color-chip); }
:focus-visible { outline: 2px solid var(--color-ink); outline-offset: 2px; }
code, pre { font-family: var(--font-mono); background: var(--color-chip); }
code { font-size: 0.8em; padding: 2px 6px; }
pre { max-width: 100%; overflow-x: auto; padding: 12px; font-size: 0.8em; }
pre code { padding: 0; font-size: inherit; }
img, svg { display: block; max-width: 100%; height: auto; }
.table-scroll { max-width: 100%; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.875em; }
th, td { padding: 8px; border-bottom: 1px solid var(--color-hairline); text-align: left; vertical-align: top; }
th { font-weight: 600; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
```

Use `.stack` for vertical groups, `.row` for wrapping labels, `.grid` for comparisons, `.panel` for a bounded group, `.callout` for a takeaway, and `.chip` for short categories. Use an ordered list for a sequence and a semantic table inside `.table-scroll` for tabular data. Keep an explicit label next to each `.value`.

Start the document with `<!doctype html>`, `<html lang="en">` (or the content's language), a meaningful `<title>`, `<meta charset="utf-8">`, and `<meta name="viewport" content="width=device-width, initial-scale=1">`. Place the content inside `<main class="graphic">`. The outer document remains naturally scrollable; do not copy the app shell's full-height layout into the graphic.
