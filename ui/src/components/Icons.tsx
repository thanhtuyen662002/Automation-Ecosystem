// ── Glassmorp Icon System ──────────────────────────────────────────────────────
// Assets từ ui/assets/ được serve qua /icons/*.svg (Vite public folder)
// Dùng <GlassIcon name="chart" /> cho các icon glassmorphism đẹp
// Dùng lucide-react cho các icon không có trong assets
import React from 'react';

// ── Danh sách tất cả icons có trong /public/icons/ ───────────────────────────
export type GlassIconName =
  | 'add-circle' | 'airplane' | 'apple' | 'arrow-circle-down' | 'arrow-circle-left'
  | 'arrow-circle-right' | 'arrow-circle-up' | 'arrows-square-up-down' | 'badge'
  | 'bell' | 'bookmark' | 'calendar' | 'camera' | 'cart' | 'chart' | 'check-circle'
  | 'clipboard' | 'cloud-sun' | 'cloud' | 'compass' | 'credit-card' | 'cross-circle'
  | 'currency' | 'database' | 'document' | 'download' | 'eye' | 'filter' | 'fire'
  | 'folder' | 'gift' | 'heart' | 'home' | 'image' | 'info' | 'key' | 'leaf' | 'lock'
  | 'map-pin' | 'menu' | 'message' | 'paint-brush' | 'pc' | 'pencil' | 'pin' | 'planet'
  | 'play-circle' | 'play-circle-1' | 'play-circle-2' | 'play-circle-3' | 'play-circle-4'
  | 'puzzle' | 'remove-circle' | 'rocket' | 'save' | 'search' | 'send' | 'setting'
  | 'share' | 'shield' | 'smiley' | 'speaker' | 'suitcase' | 'trash' | 'tree' | 'upload'
  | 'user' | 'video' | 'video-1' | 'volume' | 'wallet' | 'warning';

interface GlassIconProps {
  name: GlassIconName;
  size?: number;
  className?: string;
  style?: React.CSSProperties;
}

// ── GlassIcon — renders glassmorphism SVG asset ───────────────────────────────
export function GlassIcon({ name, size = 44, className, style }: GlassIconProps) {
  return (
    <img
      src={`/icons/${name}.svg`}
      alt={name}
      width={size}
      height={size}
      className={className}
      style={{ display: 'inline-block', flexShrink: 0, ...style }}
      draggable={false}
    />
  );
}

// ── GlassIconBadge — icon trong vòng tròn glass (dùng cho KPI cards) ─────────
interface GlassIconBadgeProps {
  name: GlassIconName;
  size?: number;   // icon size
  bg?: string;     // background color of the circle
}
export function GlassIconBadge({ name, size = 36, bg }: GlassIconBadgeProps) {
  return (
    <div style={{
      width: size + 16, height: size + 16,
      borderRadius: '50%',
      background: bg ?? 'rgba(255,255,255,0.60)',
      border: '1px solid rgba(255,255,255,0.75)',
      backdropFilter: 'blur(8px)',
      WebkitBackdropFilter: 'blur(8px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      flexShrink: 0,
      boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
    }}>
      <GlassIcon name={name} size={size} />
    </div>
  );
}

// ── Icon → Asset mapping table ────────────────────────────────────────────────
// Dùng để map từ semantic name sang asset file name
export const ICON_MAP = {
  // Dashboard / Analytics
  chart:        'chart',
  analytics:    'chart',
  views:        'eye',
  revenue:      'currency',
  performance:  'rocket',
  growth:       'arrow-circle-up',
  trend:        'chart',

  // Operations
  queue:        'clipboard',
  jobs:         'arrows-square-up-down',
  artifacts:    'video',
  pipeline:     'arrows-square-up-down',
  content:      'document',
  publish:      'send',
  approve:      'check-circle',
  reject:       'cross-circle',

  // Fleet
  fleet:        'shield',
  account:      'user',
  accounts:     'user',
  identity:     'key',
  fingerprint:  'key',
  health:       'heart',
  risk:         'warning',
  freeze:       'lock',

  // Strategy
  brain:        'planet',
  niche:        'compass',
  override:     'warning',
  strategy:     'rocket',

  // Settings
  settings:     'setting',
  policy:       'shield',
  license:      'badge',
  advanced:     'puzzle',

  // Status
  success:      'check-circle',
  error:        'cross-circle',
  warning:      'warning',
  info:         'info',
  live:         'cloud-sun',
  websocket:    'cloud',

  // Actions
  add:          'add-circle',
  delete:       'trash',
  edit:         'pencil',
  download:     'download',
  upload:       'upload',
  search:       'search',
  filter:       'filter',
  share:        'share',
  save:         'save',
  refresh:      'arrows-square-up-down',
  fire:         'fire',
  email:        'message',
  calendar:     'calendar',
  camera:       'camera',
  gift:         'gift',
} as const;
