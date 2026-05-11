// ── Fleet Health — 1:1 mapping to fleet-health accounts ──────────────────────
// Spec: ALL accounts visible. Each = its own card. Signals: risk, trust, fatigue, phase.
// Actionable accounts → DecisionBlock with action. Healthy → AccountStatusCard.
import React, { useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { DecisionBlock, ConfirmDialog } from '@/components/ui';
import { useFleetAccounts, useFreezeAccount, useClearCooldown } from '@/lib/hooks';

type Account = {
  account_id: string; phase: string; risk_level: string;
  trust_score: number; fatigue_level: number; anomaly_count: number;
  cooldown_remaining_hours: number; uploads_suspended: boolean;
  operating_mode: string; current_intent?: string; account_age_days?: number;
};

// V3/V4 fix: Healthy accounts get their OWN visible card showing all signals
function AccountStatusCard({ a }: { a: Account }) {
  const trustColor   = a.trust_score  >= 0.75 ? 'var(--success)' : a.trust_score  >= 0.50 ? 'var(--warning)' : 'var(--danger)';
  const fatigueColor = a.fatigue_level <= 0.40 ? 'var(--success)' : a.fatigue_level <= 0.65 ? 'var(--warning)' : 'var(--danger)';
  return (
    <div style={{
      padding: '0.875rem 1rem', background: 'var(--surface)',
      border: '1px solid var(--border)', borderLeft: '4px solid var(--success)',
      borderRadius: 'var(--radius)', display: 'flex', gap: '1rem',
      alignItems: 'center', flexWrap: 'wrap',
    }}>
      {/* Account ID + phase */}
      <div style={{ minWidth: 90 }}>
        <div style={{ fontWeight: 700, fontSize: '0.875rem' }}>{a.account_id}</div>
        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.15rem' }}>{a.phase}</div>
      </div>

      {/* Visible signals: risk, trust, fatigue, intent */}
      <div style={{ display: 'flex', gap: '1.25rem', flex: 1, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Rủi ro</div>
          <div style={{ fontWeight: 600, fontSize: '0.8rem', color: 'var(--success)' }}>Thấp</div>
        </div>
        <div>
          <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Trust</div>
          <div style={{ fontWeight: 700, fontSize: '0.875rem', color: trustColor }}>{Math.round(a.trust_score * 100)}%</div>
        </div>
        <div>
          <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Mệt Mỏi</div>
          <div style={{ fontWeight: 700, fontSize: '0.875rem', color: fatigueColor }}>{Math.round(a.fatigue_level * 100)}%</div>
        </div>
        <div>
          <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Bất Thường</div>
          <div style={{ fontWeight: 700, fontSize: '0.875rem', color: a.anomaly_count > 0 ? 'var(--warning)' : 'var(--text-secondary)' }}>{a.anomaly_count}</div>
        </div>
        <div>
          <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Trạng Thái</div>
          <div style={{ fontWeight: 600, fontSize: '0.8rem', color: 'var(--success)' }}>
            {a.current_intent ?? a.operating_mode}
          </div>
        </div>
        {a.uploads_suspended && (
          <div style={{ fontSize: '0.7rem', color: 'var(--warning)', fontWeight: 600, alignSelf: 'center' }}>⏸ Upload tạm dừng</div>
        )}
      </div>
      <div style={{ fontSize: '0.7rem', color: 'var(--success)', fontWeight: 600 }}>✓ Đang Ổn</div>
    </div>
  );
}

function needsAction(a: Account) {
  return a.risk_level === 'high' || a.anomaly_count >= 2 ||
    a.fatigue_level > 0.70 || (a.cooldown_remaining_hours > 0 && a.anomaly_count > 0);
}

function toDecisionProps(a: Account, onFreeze: () => void, onClear: () => void) {
  if (a.risk_level === 'high' || a.anomaly_count >= 2) {
    return {
      badge: 'NGUY HIỂM', badgeColor: 'var(--danger)',
      title: `${a.account_id} — Có ${a.anomaly_count} bất thường`,
      reason: `Pha: ${a.phase} · Chế độ: ${a.operating_mode}`,
      ev: undefined,
      confidence: undefined,
      risk: 'high' as const,
      riskFlags: a.anomaly_count >= 2 ? [`${a.anomaly_count} bất thường phát hiện`] : undefined,
      ifSkip: 'Tài khoản có thể bị ban vĩnh viễn — mất toàn bộ nội dung đã đăng',
      action: { label: '🔒 Đóng Băng Ngay', onClick: onFreeze, danger: true },
      passive: { label: 'Chỉ Giám Sát', onClick: undefined },
    };
  }
  if (a.phase === 'COOLDOWN' && a.cooldown_remaining_hours > 0) {
    return {
      badge: 'COOLDOWN', badgeColor: 'var(--warning)',
      title: `${a.account_id} — Còn ${a.cooldown_remaining_hours.toFixed(1)}h cooldown`,
      reason: `Trust: ${Math.round(a.trust_score * 100)}% · Mệt mỏi: ${Math.round(a.fatigue_level * 100)}% · ${a.anomaly_count} bất thường`,
      ev: undefined, confidence: undefined,
      risk: 'medium' as const,
      ifSkip: 'Tài khoản không thể đăng cho đến khi hết cooldown',
      action: { label: '⚡ Xóa Cooldown', onClick: onClear },
      passive: { label: 'Để Tự Hết', onClick: undefined },
    };
  }
  if (a.fatigue_level > 0.70) {
    return {
      badge: 'MỆT MỎI', badgeColor: 'var(--warning)',
      title: `${a.account_id} — Mức mệt mỏi ${Math.round(a.fatigue_level * 100)}%`,
      reason: `Trust: ${Math.round(a.trust_score * 100)}% · Pha: ${a.phase} · Bất thường: ${a.anomaly_count}`,
      ev: undefined, confidence: undefined,
      risk: 'medium' as const,
      riskFlags: [`Fatigue ${Math.round(a.fatigue_level * 100)}% — vượt ngưỡng an toàn 70%`],
      ifSkip: 'Dễ bị phát hiện bởi nền tảng — tăng rủi ro ban tài khoản',
      action: { label: '⏸ Tạm Nghỉ 24h', onClick: onFreeze },
      passive: { label: 'Tiếp Tục', onClick: undefined },
    };
  }
  return null;
}

export function FleetHealth() {
  const { data: accounts = [], isLoading, refetch } = useFleetAccounts();
  const freezeM = useFreezeAccount();
  const clearM  = useClearCooldown();
  const [confirmFreeze, setConfirmFreeze] = useState<Account | null>(null);

  const all       = accounts as Account[];
  const actionable = all.filter(needsAction);
  const healthy    = all.filter(a => !needsAction(a));

  if (isLoading) return <div style={{ textAlign: 'center', padding: '4rem', color: 'var(--text-muted)' }}>Đang tải...</div>;

  return (
    <div style={{ maxWidth: 800, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.75rem' }}>
        <div>
          <h1 style={{ fontWeight: 800, fontSize: '1.25rem', margin: 0 }}>Sức Khỏe Đội</h1>
          <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', marginTop: '0.25rem' }}>
            {all.length} tài khoản tổng · {actionable.length} cần hành động · {healthy.length} đang ổn
          </div>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={() => refetch()} style={{ display: 'flex', gap: '0.375rem', alignItems: 'center' }}>
          <RefreshCw size={13} /> Làm Mới
        </button>
      </div>

      {/* ── ACTIONABLE — requires decision ──────────────────────────────────── */}
      {actionable.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          <div style={{ fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--danger)' }}>
            🔥 Cần Hành Động ({actionable.length})
          </div>
          {actionable.map(a => {
            const props = toDecisionProps(
              a,
              () => setConfirmFreeze(a),
              () => clearM.mutate(a.account_id)
            );
            return props ? <DecisionBlock key={a.account_id} {...props} /> : null;
          })}
        </div>
      )}

      {/* ── HEALTHY — V3/V4 fix: each account has its own visible card ────── */}
      {healthy.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <div style={{ fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--success)' }}>
            ✓ Đang Hoạt Động Tốt ({healthy.length})
          </div>
          {healthy.map(a => <AccountStatusCard key={a.account_id} a={a} />)}
        </div>
      )}

      <ConfirmDialog
        open={!!confirmFreeze}
        onClose={() => setConfirmFreeze(null)}
        onConfirm={() => {
          if (confirmFreeze) { freezeM.mutate(confirmFreeze.account_id); setConfirmFreeze(null); }
        }}
        title="Xác Nhận Đóng Băng"
        message={`Đóng băng ${confirmFreeze?.account_id}? Tất cả upload sẽ dừng ngay lập tức.`}
        danger
      />
    </div>
  );
}
