import type { SVGProps } from 'react'

// Paths are copied from the Paper frame "Chat · A2 Stillwater". Strokes use
// currentColor so each call site sets the colour from CSS.

type IconProps = { size?: number } & Omit<SVGProps<SVGSVGElement>, 'width' | 'height'>

function Icon({ size = 18, children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      focusable="false"
      style={{ display: 'inline-block', flexShrink: 0 }}
      {...rest}
    >
      {children}
    </svg>
  )
}

/** Argo's mark: a pink hut with an ink door. Fixed colours, it is a logo. */
export function HutLogo({ size = 24 }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M2 22V11l10-9 10 9v11z" fill="#FFB3E6" stroke="#111111" strokeWidth={2} />
      <circle cx="12" cy="15" r="2.5" fill="#111111" />
    </Icon>
  )
}

/** Plain outlined page, used next to file names inside messages. */
export function FileIcon({ size = 11 }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M5 2h8l6 6v14H5zM13 2v6h6" fill="none" stroke="currentColor" strokeWidth={2.25} />
    </Icon>
  )
}

/** Page with a yellow folded corner, used on composer chips. */
export function FileChipIcon({ size = 11 }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M5 2h8l6 6v14H5z" fill="none" stroke="currentColor" strokeWidth={2.5} />
      <path d="M13 2l6 6h-6z" fill="#EDF24A" stroke="currentColor" strokeWidth={2.5} />
    </Icon>
  )
}

/** Window with a left panel: toggles the sidebar between docked and collapsed. */
export function PanelIcon({ size = 18 }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M4 4h16v16H4zM9.5 4v16" fill="none" stroke="currentColor" strokeWidth={2} />
    </Icon>
  )
}

export function PlusIcon({ size = 18 }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M12 4v16M4 12h16" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="square" />
    </Icon>
  )
}

export function MicIcon({ size = 18 }: { size?: number }) {
  return (
    <Icon size={size}>
      <path
        d="M9 3h6v10H9zM5 10v3l3 4h8l3-4v-3M12 17v4M8 21h8"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="square"
      />
    </Icon>
  )
}

export function ArrowUpIcon({ size = 18 }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M12 20V5M5 11l7-7 7 7" fill="none" stroke="currentColor" strokeWidth={2.5} />
    </Icon>
  )
}

export function StopIcon({ size = 18 }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M6 6h12v12H6z" fill="currentColor" />
    </Icon>
  )
}

export function CloseIcon({ size = 10 }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M5 5l14 14M19 5L5 19" fill="none" stroke="currentColor" strokeWidth={3} strokeLinecap="square" />
    </Icon>
  )
}
