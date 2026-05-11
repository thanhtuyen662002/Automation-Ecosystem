// ── Growth Mode Impact Constants ─────────────────────────────────────────────
// Used by ActionImpactPreview to show before/after diff when mode changes.
// These mirror the actual CEO brain parameter mappings from the backend.

export interface ModeParams {
  label: string;
  color: string;
  threshold_modifier: number;
  exploration_rate: number;
  max_risk_allowed: number;
  est_content_per_day: [number, number]; // [min, max]
  est_risk_exposure: 'very low' | 'low' | 'medium' | 'high' | 'very high';
  description: string;
  warning?: string;
}

export const GROWTH_MODES: Record<string, ModeParams> = {
  conservative: {
    label: 'Conservative',
    color: 'var(--info)',
    threshold_modifier: 1.3,
    exploration_rate: 0.05,
    max_risk_allowed: 0.30,
    est_content_per_day: [2, 4],
    est_risk_exposure: 'very low',
    description: 'High bar for publishing. Only proven angles. Minimal fleet strain.',
  },
  balanced: {
    label: 'Balanced',
    color: 'var(--success)',
    threshold_modifier: 1.0,
    exploration_rate: 0.10,
    max_risk_allowed: 0.50,
    est_content_per_day: [4, 7],
    est_risk_exposure: 'medium',
    description: 'Default mode. Mixes proven content with limited exploration.',
  },
  aggressive: {
    label: 'Aggressive',
    color: 'var(--danger)',
    threshold_modifier: 0.8,
    exploration_rate: 0.20,
    max_risk_allowed: 0.75,
    est_content_per_day: [8, 12],
    est_risk_exposure: 'high',
    description: 'Lower quality threshold. High volume, higher ban risk.',
    warning: 'Increases ban probability. Use only when behind target.',
  },
  recovery: {
    label: 'Recovery',
    color: 'var(--warning)',
    threshold_modifier: 1.5,
    exploration_rate: 0.02,
    max_risk_allowed: 0.20,
    est_content_per_day: [1, 3],
    est_risk_exposure: 'very low',
    description: 'Post-ban healing mode. Very few posts. Rebuilds trust scores.',
    warning: 'Revenue will drop significantly during recovery.',
  },
  domination: {
    label: 'Domination',
    color: 'var(--primary)',
    threshold_modifier: 0.65,
    exploration_rate: 0.30,
    max_risk_allowed: 0.90,
    est_content_per_day: [14, 20],
    est_risk_exposure: 'very high',
    description: 'Maximum volume. Full fleet utilisation. Short-term revenue spike.',
    warning: 'Very high ban risk. Not recommended for sustained use.',
  },
};

// Human-readable signal names for score decomposition
export const SCORE_SIGNAL_LABELS: Record<string, { label: string; description: string; weight: number; color: string }> = {
  trend_score:     { label: 'Trend',     description: 'Keyword / product demand right now',        weight: 0.30, color: '#6366F1' },
  match_score:     { label: 'Match',     description: 'How well content fits the niche / page',     weight: 0.25, color: '#10B981' },
  novelty_score:   { label: 'Novelty',   description: 'Not a duplicate angle vs recent content',    weight: 0.20, color: '#F59E0B' },
  historical_perf: { label: 'Track Record', description: 'Similar content past performance',         weight: 0.15, color: '#3B82F6' },
  production_cost: { label: 'Cost',      description: 'Compute / API cost (penalises high cost)',   weight: -0.10, color: '#EF4444' },
};

// Plain-English translation of reason codes from the brain
export const REASON_LABELS: Record<string, string> = {
  trend_surge:      'Trending keyword detected',
  high_hook:        'Strong hook pattern (high completion rate)',
  novelty_high:     'Novel angle — not seen in recent 7 days',
  trend_match:      'Strong niche-trend alignment',
  high_views:       'Historical views above niche average',
  duplicate_angle:  '⚠ Angle similar to recent published content',
  manual_override:  'Published via manual override',
  low_novelty:      '⚠ Low novelty — angle used recently',
  high_cost:        '⚠ High production cost relative to expected value',
};

export function translateReason(reason: string): string {
  return reason
    .split('+')
    .map(r => REASON_LABELS[r.trim()] ?? r.trim())
    .join(' · ');
}
