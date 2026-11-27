import type { ReactNode } from 'react'

/** Built-in project badges — original art in an OW2 player-icon (circular cartoon) vibe. */
export type ProjectIconId =
  | 'bot' | 'meka' | 'phoenix' | 'hammer' | 'chronos'
  | 'fox' | 'owl' | 'crystal' | 'rocket' | 'shield'
  | 'ramen' | 'squid' | 'dragon' | 'star' | 'fist'
  | 'wing' | 'bolt' | 'bloom' | 'comet' | 'wolf'
  | 'wafer-blue' | 'wafer-orange' | 'wafer-purple' | 'wafer-teal'
  | 'mdp-blue' | 'mdp-orange' | 'mdp-purple' | 'mdp-teal'
  | 'etch-blue' | 'etch-orange' | 'etch-purple' | 'etch-teal'
  | 'cvd-blue' | 'cvd-orange' | 'cvd-purple' | 'cvd-teal'

export interface ProjectIconDef {
  id: ProjectIconId
  /** Chinese label shown in the picker. */
  label: string
  bg: string
  art: ReactNode
}

function Frame({ bg, children }: { bg: string; children: ReactNode }) {
  return (
    <>
      <circle cx="32" cy="32" r="32" fill={bg} />
      <circle cx="32" cy="32" r="30.5" fill="none" stroke="rgba(255,255,255,0.22)" strokeWidth="1.5" />
      <ellipse cx="22" cy="18" rx="14" ry="8" fill="rgba(255,255,255,0.18)" />
      {children}
    </>
  )
}

/** Shared wafer disk with flat notch — used by semiconductor process badges. */
function WaferDisk({ fill = '#f2f5f8', stroke = 'rgba(20,24,32,0.16)' }: { fill?: string; stroke?: string }) {
  return (
    <>
      <circle cx="32" cy="33" r="15" fill={fill} stroke={stroke} strokeWidth="1.2" />
      <path d="M44.5 40.5 40 45.2A15 15 0 0 0 44.5 40.5Z" fill={fill} />
      <path d="M43.2 39.4l-3.2 3.4" stroke={stroke} strokeWidth="1.2" strokeLinecap="round" />
    </>
  )
}

function WaferGrid() {
  return (
    <>
      {[24, 32, 40].map((x) => (
        <line key={`v${x}`} x1={x} y1="22" x2={x} y2="44" stroke="rgba(20,24,32,0.18)" strokeWidth="1" />
      ))}
      {[25, 33, 41].map((y) => (
        <line key={`h${y}`} x1="20" y1={y} x2="44" y2={y} stroke="rgba(20,24,32,0.18)" strokeWidth="1" />
      ))}
    </>
  )
}

const SEMI_PALETTE = [
  { key: 'blue', label: '蓝', bg: '#3d7cff' },
  { key: 'orange', label: '橙', bg: '#ff9f0a' },
  { key: 'purple', label: '紫', bg: '#bf5af2' },
  { key: 'teal', label: '青', bg: '#30b0c7' },
] as const

type SemiColor = (typeof SEMI_PALETTE)[number]

function semiIcons(): ProjectIconDef[] {
  const motifs: {
    kind: 'wafer' | 'mdp' | 'etch' | 'cvd'
    title: string
    art: (c: SemiColor) => ReactNode
  }[] = [
    {
      kind: 'wafer',
      title: '晶圆',
      art: (c) => (
        <Frame bg={c.bg}>
          <WaferDisk />
          <WaferGrid />
          <circle cx="27" cy="29" r="1.6" fill="#ffd60a" />
          <circle cx="37" cy="37" r="1.3" fill="#5ac8fa" />
        </Frame>
      ),
    },
    {
      kind: 'mdp',
      title: 'MDP',
      art: (c) => (
        <Frame bg={c.bg}>
          <WaferDisk fill="#eef2f7" />
          <WaferGrid />
          {/* Metal film coating the top of the wafer */}
          <path d="M18 30a14 14 0 0 1 28 0" fill="#ffd60a" opacity="0.92" />
          <path d="M20 30a12 12 0 0 1 24 0" fill="#ffe566" opacity="0.55" />
          {/* Sputter droplets */}
          <circle cx="24" cy="16" r="2.2" fill="#ffd60a" />
          <circle cx="32" cy="13" r="2.8" fill="#fff3b0" />
          <circle cx="40" cy="17" r="2" fill="#ffd60a" />
          <path d="M24 18v4M32 16v5M40 19v3" stroke="#fff8dc" strokeWidth="1.5" strokeLinecap="round" opacity="0.85" />
        </Frame>
      ),
    },
    {
      kind: 'etch',
      title: 'Etch',
      art: (c) => (
        <Frame bg={c.bg}>
          <WaferDisk fill="#e8eef6" />
          {/* Etched channels */}
          <path d="M22 28h20M22 34h14M28 40h12" stroke="#1c1c1e" strokeWidth="2.2" strokeLinecap="round" opacity="0.55" />
          <path d="M22 28h20M22 34h14M28 40h12" stroke="#fff" strokeWidth="1" strokeLinecap="round" opacity="0.35" />
          {/* Plasma bolts */}
          <path d="M18 14l4 8-5 2 8 10" fill="none" stroke="#b8f3ff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M46 12l-3 7 5 2-7 9" fill="none" stroke="#7ad7ff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
          <circle cx="32" cy="18" r="3" fill="#ffd60a" />
        </Frame>
      ),
    },
    {
      kind: 'cvd',
      title: 'CVD',
      art: (c) => (
        <Frame bg={c.bg}>
          <WaferDisk fill="#f4f7fb" />
          {/* Concentric deposited films */}
          <circle cx="32" cy="33" r="11" fill="none" stroke="#a8e0ff" strokeWidth="2.2" />
          <circle cx="32" cy="33" r="7" fill="none" stroke="#7ad7ff" strokeWidth="2" />
          <circle cx="32" cy="33" r="3.2" fill="#5ac8fa" />
          {/* Soft vapor clouds */}
          <ellipse cx="20" cy="16" rx="6" ry="3.5" fill="#fff" opacity="0.85" />
          <ellipse cx="28" cy="14" rx="5" ry="3" fill="#e6f7ff" opacity="0.9" />
          <ellipse cx="42" cy="17" rx="5.5" ry="3.2" fill="#fff" opacity="0.8" />
          <circle cx="36" cy="12" r="2" fill="#c8eeff" />
        </Frame>
      ),
    },
  ]

  return motifs.flatMap((motif) =>
    SEMI_PALETTE.map((color) => ({
      id: `${motif.kind}-${color.key}` as ProjectIconId,
      label: `${motif.title}·${color.label}`,
      bg: color.bg,
      art: motif.art(color),
    })),
  )
}

const SEMI_ICONS = semiIcons()

export const PROJECT_ICONS: ProjectIconDef[] = [
  {
    id: 'bot',
    label: '训练机器人',
    bg: '#3d7cff',
    art: (
      <Frame bg="#3d7cff">
        <rect x="18" y="20" width="28" height="24" rx="6" fill="#e8f0ff" />
        <circle cx="24" cy="30" r="3.2" fill="#1a3a8a" />
        <circle cx="40" cy="30" r="3.2" fill="#1a3a8a" />
        <rect x="26" y="36" width="12" height="3" rx="1.5" fill="#1a3a8a" />
        <rect x="28" y="14" width="8" height="8" rx="2" fill="#ffd60a" />
        <circle cx="32" cy="18" r="2" fill="#fff" />
      </Frame>
    ),
  },
  {
    id: 'meka',
    label: '机甲兔',
    bg: '#ff4d8d',
    art: (
      <Frame bg="#ff4d8d">
        <ellipse cx="22" cy="16" rx="5" ry="10" fill="#fff" transform="rotate(-18 22 16)" />
        <ellipse cx="42" cy="16" rx="5" ry="10" fill="#fff" transform="rotate(18 42 16)" />
        <ellipse cx="22" cy="16" rx="2.2" ry="6" fill="#ffb3d0" transform="rotate(-18 22 16)" />
        <ellipse cx="42" cy="16" rx="2.2" ry="6" fill="#ffb3d0" transform="rotate(18 42 16)" />
        <circle cx="32" cy="34" r="14" fill="#fff" />
        <circle cx="26" cy="32" r="2.4" fill="#2b1a24" />
        <circle cx="38" cy="32" r="2.4" fill="#2b1a24" />
        <path d="M28 40c2 2.4 6 2.4 8 0" fill="none" stroke="#2b1a24" strokeWidth="2" strokeLinecap="round" />
        <path d="M20 30h-6l3 5 3-5zm24 0h6l-3 5-3-5z" fill="#ffd60a" />
      </Frame>
    ),
  },
  {
    id: 'phoenix',
    label: '火鸟',
    bg: '#ff6b2c',
    art: (
      <Frame bg="#ff6b2c">
        <path d="M32 14c6 6 14 10 16 18-6-2-10 0-16 6-6-6-10-8-16-6 2-8 10-12 16-18z" fill="#ffe566" />
        <path d="M32 28c3 4 7 6 8 10-3-1-5 0-8 3-3-3-5-4-8-3 1-4 5-6 8-10z" fill="#ff9f1a" />
        <circle cx="32" cy="22" r="2.5" fill="#7a1f0a" />
      </Frame>
    ),
  },
  {
    id: 'hammer',
    label: '战锤',
    bg: '#5ac8fa',
    art: (
      <Frame bg="#5ac8fa">
        <rect x="29" y="22" width="6" height="26" rx="2" fill="#f5f7fa" />
        <path d="M16 20h32l-4 12H20z" fill="#ffd60a" stroke="#c78b00" strokeWidth="1" />
        <rect x="18" y="18" width="28" height="6" rx="2" fill="#fff" />
      </Frame>
    ),
  },
  {
    id: 'chronos',
    label: '时空',
    bg: '#7c5cff',
    art: (
      <Frame bg="#7c5cff">
        <circle cx="32" cy="32" r="14" fill="none" stroke="#e8e0ff" strokeWidth="3.5" />
        <circle cx="32" cy="32" r="3" fill="#ffd60a" />
        <path d="M32 22v10l7 4" fill="none" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" />
        <path d="M18 18c8 2 12 8 14 14" fill="none" stroke="#ff9f0a" strokeWidth="2" strokeLinecap="round" opacity="0.85" />
      </Frame>
    ),
  },
  {
    id: 'fox',
    label: '狐狸',
    bg: '#ff9f0a',
    art: (
      <Frame bg="#ff9f0a">
        <path d="M16 40 L20 18 L32 28 L44 18 L48 40 Z" fill="#fff3e0" />
        <path d="M20 18l6 14h-8zm28 0l-6 14h8z" fill="#ff6b2c" />
        <circle cx="26" cy="34" r="2.2" fill="#3a1a00" />
        <circle cx="38" cy="34" r="2.2" fill="#3a1a00" />
        <ellipse cx="32" cy="40" rx="3" ry="2" fill="#3a1a00" />
      </Frame>
    ),
  },
  {
    id: 'owl',
    label: '猫头鹰',
    bg: '#34c759',
    art: (
      <Frame bg="#34c759">
        <ellipse cx="32" cy="36" rx="16" ry="14" fill="#e8ffe8" />
        <circle cx="24" cy="34" r="7" fill="#fff" />
        <circle cx="40" cy="34" r="7" fill="#fff" />
        <circle cx="24" cy="34" r="3" fill="#1a3a1a" />
        <circle cx="40" cy="34" r="3" fill="#1a3a1a" />
        <path d="M30 42l2 4 2-4z" fill="#ff9f0a" />
        <path d="M18 22l8 8M46 22l-8 8" stroke="#c8f5c8" strokeWidth="3" strokeLinecap="round" />
      </Frame>
    ),
  },
  {
    id: 'crystal',
    label: '水晶',
    bg: '#64d2ff',
    art: (
      <Frame bg="#0a84ff">
        <path d="M32 12l12 16-12 24L20 28z" fill="#b8ecff" />
        <path d="M32 12l12 16H32z" fill="#7ad7ff" />
        <path d="M32 12L20 28h12z" fill="#e6f9ff" />
        <path d="M20 28l12 24V28z" fill="#4ec3f7" />
        <path d="M32 28v24l12-24z" fill="#2bb0ed" />
      </Frame>
    ),
  },
  {
    id: 'rocket',
    label: '火箭',
    bg: '#ff453a',
    art: (
      <Frame bg="#ff453a">
        <path d="M32 12c8 10 10 22 8 30h-16c-2-8 0-20 8-30z" fill="#fff" />
        <circle cx="32" cy="30" r="4" fill="#5ac8fa" />
        <path d="M24 42l-6 10 8-4zm16 0l6 10-8-4z" fill="#ffd60a" />
        <path d="M28 48h8l-2 8h-4z" fill="#ff9f0a" />
      </Frame>
    ),
  },
  {
    id: 'shield',
    label: '护盾',
    bg: '#30d158',
    art: (
      <Frame bg="#248a3d">
        <path d="M32 12l16 6v14c0 12-8 20-16 24-8-4-16-12-16-24V18z" fill="#7aef9a" />
        <path d="M32 18l10 4v10c0 8-5 14-10 17-5-3-10-9-10-17V22z" fill="#fff" opacity="0.9" />
        <path d="M32 26v16M26 34h12" stroke="#248a3d" strokeWidth="2.5" strokeLinecap="round" />
      </Frame>
    ),
  },
  {
    id: 'ramen',
    label: '拉面',
    bg: '#ffd60a',
    art: (
      <Frame bg="#ff9f0a">
        <ellipse cx="32" cy="28" rx="18" ry="8" fill="#fff8e0" />
        <path d="M14 28c0 14 8 22 18 22s18-8 18-22" fill="#fff" />
        <path d="M20 30c4 2 8 2 12 0M24 34c3 1.5 6 1.5 9 0" stroke="#e85d04" strokeWidth="2" strokeLinecap="round" fill="none" />
        <circle cx="28" cy="38" r="2.5" fill="#ff453a" />
        <circle cx="36" cy="40" r="2" fill="#34c759" />
        <path d="M40 22c0-6 4-10 4-10s2 6 0 12" stroke="#fff" strokeWidth="2" fill="none" strokeLinecap="round" />
      </Frame>
    ),
  },
  {
    id: 'squid',
    label: '鱿鱼',
    bg: '#bf5af2',
    art: (
      <Frame bg="#bf5af2">
        <ellipse cx="32" cy="28" rx="14" ry="12" fill="#f3e8ff" />
        <circle cx="26" cy="26" r="3" fill="#3b1a5c" />
        <circle cx="38" cy="26" r="3" fill="#3b1a5c" />
        <circle cx="27" cy="25" r="1" fill="#fff" />
        <circle cx="39" cy="25" r="1" fill="#fff" />
        <path d="M22 38c0 8-4 14-4 14M28 40v14M36 40v14M42 38c0 8 4 14 4 14" stroke="#e0c4ff" strokeWidth="3.5" strokeLinecap="round" />
        <ellipse cx="32" cy="34" rx="4" ry="2.5" fill="#d4a5ff" />
      </Frame>
    ),
  },
  {
    id: 'dragon',
    label: '龙',
    bg: '#ff375f',
    art: (
      <Frame bg="#ff375f">
        <path d="M14 36c8-16 28-20 36-8-6 2-10 8-10 8l8 4c-10 8-26 6-34-4z" fill="#ffe0e8" />
        <path d="M40 24l8-8 2 8-6 4z" fill="#ffd60a" />
        <circle cx="28" cy="30" r="2.5" fill="#3a0010" />
        <path d="M18 34c4 2 8 2 12 0" fill="none" stroke="#3a0010" strokeWidth="2" strokeLinecap="round" />
        <path d="M14 38l-4 6M18 40l-2 8" stroke="#ff9f0a" strokeWidth="2.5" strokeLinecap="round" />
      </Frame>
    ),
  },
  {
    id: 'star',
    label: '星辉',
    bg: '#ffd60a',
    art: (
      <Frame bg="#ff9f0a">
        <path d="M32 12l4.5 12.5H50l-11 8.5 4 13L32 38l-11 8 4-13-11-8.5h13.5z" fill="#fff8dc" />
        <circle cx="32" cy="30" r="4" fill="#ff6b00" />
      </Frame>
    ),
  },
  {
    id: 'fist',
    label: '铁拳',
    bg: '#ff6b00',
    art: (
      <Frame bg="#c93400">
        <rect x="18" y="24" width="28" height="20" rx="6" fill="#ffd0b0" />
        <rect x="20" y="20" width="6" height="12" rx="3" fill="#ffe0cc" />
        <rect x="28" y="18" width="6" height="14" rx="3" fill="#ffe0cc" />
        <rect x="36" y="20" width="6" height="12" rx="3" fill="#ffe0cc" />
        <rect x="22" y="40" width="20" height="8" rx="3" fill="#ff9f0a" />
      </Frame>
    ),
  },
  {
    id: 'wing',
    label: '飞翼',
    bg: '#64d2ff',
    art: (
      <Frame bg="#0077c8">
        <path d="M32 40c-12-4-18-16-18-24 10 4 16 12 18 24z" fill="#c8eeff" />
        <path d="M32 40c12-4 18-16 18-24-10 4-16 12-18 24z" fill="#e6f7ff" />
        <path d="M32 22v24" stroke="#fff" strokeWidth="3" strokeLinecap="round" />
        <circle cx="32" cy="20" r="4" fill="#ffd60a" />
      </Frame>
    ),
  },
  {
    id: 'bolt',
    label: '闪电',
    bg: '#ffd60a',
    art: (
      <Frame bg="#1c1c1e">
        <path d="M36 10L22 34h10l-4 20 18-28H34l2-16z" fill="#ffd60a" />
        <path d="M34 14l-8 16h7l-2 12 10-16h-7z" fill="#fff3b0" opacity="0.7" />
      </Frame>
    ),
  },
  {
    id: 'bloom',
    label: '樱花',
    bg: '#ff9ecd',
    art: (
      <Frame bg="#ff6b9d">
        {[0, 72, 144, 216, 288].map((deg) => (
          <ellipse
            key={deg}
            cx="32"
            cy="22"
            rx="7"
            ry="11"
            fill="#ffe0ef"
            transform={`rotate(${deg} 32 32)`}
          />
        ))}
        <circle cx="32" cy="32" r="5" fill="#ffd60a" />
      </Frame>
    ),
  },
  {
    id: 'comet',
    label: '彗星',
    bg: '#5e5ce6',
    art: (
      <Frame bg="#5e5ce6">
        <path d="M14 40c10-6 20-16 28-28" fill="none" stroke="#c7c5ff" strokeWidth="5" strokeLinecap="round" opacity="0.5" />
        <path d="M16 42c10-6 18-14 26-24" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" />
        <circle cx="44" cy="16" r="8" fill="#ffd60a" />
        <circle cx="44" cy="16" r="4" fill="#fff8dc" />
      </Frame>
    ),
  },
  {
    id: 'wolf',
    label: '苍狼',
    bg: '#8e8e93',
    art: (
      <Frame bg="#3a3a3c">
        <path d="M14 42 L18 16 L32 26 L46 16 L50 42 Z" fill="#d1d1d6" />
        <path d="M18 16l8 14h-8zm28 0l-8 14h8z" fill="#636366" />
        <circle cx="26" cy="34" r="2.4" fill="#ffd60a" />
        <circle cx="38" cy="34" r="2.4" fill="#ffd60a" />
        <path d="M32 36l-3 5h6z" fill="#1c1c1e" />
        <path d="M26 44c4 3 8 3 12 0" fill="none" stroke="#1c1c1e" strokeWidth="2" strokeLinecap="round" />
      </Frame>
    ),
  },
  ...SEMI_ICONS,
]

export const PROJECT_ICON_IDS = PROJECT_ICONS.map((i) => i.id)

export function projectIconDef(id?: string | null): ProjectIconDef | undefined {
  if (!id) return undefined
  return PROJECT_ICONS.find((i) => i.id === id)
}

export function ProjectBadge({
  icon,
  color,
  label,
  size = 40,
  className = 'project-logo',
  title,
}: {
  icon?: string | null
  color: string
  /** Fallback text when no icon (usually project key slice). */
  label: string
  size?: number
  className?: string
  title?: string
}) {
  const def = projectIconDef(icon)
  if (def) {
    return (
      <span
        className={`${className} project-badge-icon`}
        title={title ?? def.label}
        style={{ width: size, height: size, background: 'transparent', boxShadow: 'none' }}
      >
        <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden>
          {def.art}
        </svg>
      </span>
    )
  }
  return (
    <span
      className={className}
      title={title}
      style={{ width: size, height: size, background: color, fontSize: size * 0.34 }}
    >
      {label}
    </span>
  )
}
