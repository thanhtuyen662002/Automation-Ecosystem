// ── Glassmorp Design System ──────────────────────────────────────────────────
// Extracted from: tailwinddashboard.com/demo/?demo=glassmorp
// Visual decomposition: light lavender bg, white cards, violet #7c3aed accents

export const DS = {
  // ── Color Palette (EXACT from Glassmorp template) ───────────────────────────
  colors: {
    // Page background — very light lavender
    bg:           '#f5f3ff',
    bgSoft:       '#faf9ff',

    // Sidebar — pure white
    sidebar:      '#ffffff',
    sidebarBorder: '#ede9f8',

    // Cards — pure white
    card:         '#ffffff',
    cardBorder:   '#ede9f8',

    // Primary violet (Glassmorp signature)
    primary:      '#7c3aed',
    primaryHover: '#6d28d9',
    primaryLight: '#8b5cf6',
    primaryGlow:  '#a78bfa',
    primaryMuted: 'rgba(124, 58, 237, 0.08)',
    primarySoft:  'rgba(124, 58, 237, 0.05)',

    // Sidebar active state
    activeNavBg:   'rgba(124, 58, 237, 0.08)',
    activeNavText: '#7c3aed',

    // Semantic
    success:      '#10b981',
    successMuted: 'rgba(16, 185, 129, 0.10)',
    warning:      '#f59e0b',
    warningMuted: 'rgba(245, 158, 11, 0.10)',
    danger:       '#ef4444',
    dangerMuted:  'rgba(239, 68, 68, 0.10)',
    info:         '#3b82f6',
    infoMuted:    'rgba(59, 130, 246, 0.10)',
    pink:         '#ec4899',
    pinkMuted:    'rgba(236, 72, 153, 0.10)',
    teal:         '#14b8a6',

    // Text hierarchy
    textPrimary:   '#1e1b4b',  // dark navy/purple
    textSecondary: '#64748b',  // medium gray
    textMuted:     '#94a3b8',  // light gray

    // Chart blob colors (inside chart cards)
    blobPurple: 'rgba(167, 139, 250, 0.50)',
    blobOrange: 'rgba(251, 146, 60, 0.45)',
    blobPink:   'rgba(236, 72, 153, 0.35)',

    // Chart area fills
    chartPurple: 'rgba(139, 92, 246, 0.45)',
    chartTeal:   'rgba(20, 184, 166, 0.30)',
  },

  // ── Typography ────────────────────────────────────────────────────────────
  typography: {
    fontFamily: "'Inter', system-ui, sans-serif",
    sizes: {
      pageTitle:    '1.375rem',  // 22px — page h1
      cardTitle:    '0.9375rem', // 15px — card headings
      metricNumber: '1.75rem',   // 28px — KPI values
      label:        '0.72rem',   // 11.5px — uppercase labels
      body:         '0.8125rem', // 13px — default body
      caption:      '0.75rem',   // 12px — secondary text
      micro:        '0.7rem',    // 11px — badges, tags
    },
    weights: {
      regular: 400,
      medium:  500,
      semibold: 600,
      bold:    700,
      black:   800,
    },
  },

  // ── Spacing (8px system) ─────────────────────────────────────────────────
  spacing: {
    xs:  '4px',
    sm:  '8px',
    md:  '12px',
    lg:  '16px',
    xl:  '20px',
    xxl: '24px',
    xxxl: '32px',
  },

  // ── Shape ─────────────────────────────────────────────────────────────────
  radius: {
    sm:  '8px',
    md:  '12px',   // var(--radius)
    lg:  '16px',
    xl:  '20px',
    full: '9999px',
  },

  // ── Shadows ───────────────────────────────────────────────────────────────
  shadows: {
    card:      '0 1px 3px rgba(0,0,0,0.05), 0 4px 20px rgba(124,58,237,0.06)',
    cardHover: '0 4px 24px rgba(124,58,237,0.14), 0 1px 4px rgba(0,0,0,0.06)',
    btn:       '0 4px 14px rgba(124,58,237,0.35)',
    sidebar:   '1px 0 0 #ede9f8',
  },

  // ── Component dimensions ──────────────────────────────────────────────────
  layout: {
    sidebarWidth:    '240px',
    sidebarCollapsed: '64px',
    topbarHeight:    '56px',
    cardPadding:     '20px',
    pagePadding:     '24px 28px',
  },

  // ── KPI Icon Colors (Glassmorp stat cards) ────────────────────────────────
  kpiIconColors: {
    sessions:   { bg: 'rgba(20,184,166,0.12)',  color: '#14b8a6' },  // teal
    revenue:    { bg: 'rgba(245,158,11,0.12)',  color: '#f59e0b' },  // amber
    bounce:     { bg: 'rgba(59,130,246,0.12)',  color: '#3b82f6' },  // blue
    users:      { bg: 'rgba(124,58,237,0.10)',  color: '#7c3aed' },  // violet
    growth:     { bg: 'rgba(16,185,129,0.10)',  color: '#10b981' },  // green
    danger:     { bg: 'rgba(239,68,68,0.10)',   color: '#ef4444' },  // red
  },
} as const;

export type DesignSystem = typeof DS;
