// ── Content Queue — Real Data from GET /api/v1/brain/queue?status=all ─────────
import React, { useEffect } from 'react';
import { CheckCheck, AlertTriangle, Clock, CheckCircle, XCircle, RefreshCw } from 'lucide-react';
import { DecisionBlock, EmptyState } from '@/components/ui';
import { useQueue, useApproveContent, useRejectContent } from '@/lib/hooks';
import { useUIStore } from '@/lib/store';
import { fmtCurrency } from '@/lib/utils';

// Real brain_queue fields (verified against content_brain.py schema):
// content_id, platform, niche, mode, status, final_score, expected_value,
// confidence, hook, risk_flags[], reason, priority_score, created_at
type QueueItem = {
  content_id: string; platform: string; niche: string; mode: string;
  status: 'pending' | 'approved' | 'rejected' | 'force_published';
  final_score: number; expected_value: number; confidence: number;
  hook: string; risk_flags: string[]; reason: string; priority_score: number;
  created_at: number; approved_by?: string;
};

function StatusChip({ status }: { status: string }) {
  const cfg: Record<string, { color: string; bg: string; icon: React.ReactNode; label: string }> = {
    approved:       { color: 'var(--success)', bg: 'var(--success-muted)', icon: <CheckCircle size={11} />, label: 'Đã Đăng' },
    force_published:{ color: 'var(--success)', bg: 'var(--success-muted)', icon: <CheckCircle size={11} />, label: 'Đã Bắt Buộc' },
    rejected:       { color: 'var(--danger)',  bg: 'var(--danger-muted)',  icon: <XCircle size={11} />,   label: 'Đã Từ Chối' },
    pending:        { color: 'var(--warning)', bg: 'var(--warning-muted)', icon: <Clock size={11} />,     label: 'Chờ Duyệt' },
  };
  const c = cfg[status] ?? { color: 'var(--text-muted)', bg: 'var(--surface-2)', icon: null, label: status };
  return (
    <span style={{ display: 'inline-flex', gap: '0.3rem', alignItems: 'center', padding: '0.2rem 0.6rem', borderRadius: '9999px', background: c.bg, color: c.color, fontSize: '0.7rem', fontWeight: 700 }}>
      {c.icon}{c.label}
    </span>
  );
}

// Error state for API failures
function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div style={{ padding: '2rem', textAlign: 'center', background: 'var(--surface)', border: '1px solid var(--danger)', borderRadius: 'var(--radius)' }}>
      <div style={{ fontSize: '1.5rem', marginBottom: '0.5rem' }}>⚠️</div>
      <div style={{ color: 'var(--danger)', fontWeight: 600, marginBottom: '0.5rem' }}>Không thể tải dữ liệu</div>
      <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', marginBottom: '1rem' }}>{message}</div>
      <button className="btn btn-secondary btn-sm" onClick={onRetry}>
        <RefreshCw size={13} /> Thử lại
      </button>
    </div>
  );
}

export function ContentQueue() {
  const { autoApprove, setPendingCount } = useUIStore();
  const { data: rawQueue, isLoading, error, refetch } = useQueue();

  const approveM = useApproveContent();
  const rejectM  = useRejectContent();

  const queue   = (rawQueue ?? []) as QueueItem[];
  const pending  = queue.filter(i => i.status === 'pending').sort((a, b) => b.priority_score - a.priority_score);
  const approved = queue.filter(i => i.status === 'approved' || i.status === 'force_published');
  const rejected = queue.filter(i => i.status === 'rejected');

  const avgEv     = pending.reduce((s, i) => s + i.expected_value, 0) / (pending.length || 1);
  const highValue  = pending.filter(i => i.expected_value >= avgEv);
  const totalEv    = highValue.reduce((s, i) => s + i.expected_value, 0);

  useEffect(() => { setPendingCount(pending.length); }, [pending.length, setPendingCount]);

  function approve(id: string) { approveM.mutate(id); }
  function reject(id: string)  { rejectM.mutate({ id }); }
  function approveAll()        { highValue.forEach(i => approve(i.content_id)); }

  if (isLoading) return (
    <div style={{ textAlign: 'center', padding: '4rem', color: 'var(--text-muted)' }}>Đang tải hàng chờ...</div>
  );

  if (error) return (
    <ErrorState message={(error as Error).message} onRetry={() => refetch()} />
  );

  return (
    <div style={{ maxWidth: 760, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.75rem' }}>
        <div>
          <h1 style={{ fontWeight: 800, fontSize: '1.25rem', margin: 0 }}>Hàng Chờ Nội Dung</h1>
          <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', marginTop: '0.25rem' }}>
            {pending.length} chờ duyệt · {approved.length} đã đăng · {rejected.length} đã từ chối
            {pending.length > 0 && ` · Tổng EV ${fmtCurrency(pending.reduce((s, i) => s + i.expected_value, 0))}`}
          </div>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          {highValue.length > 0 && (
            <button className="btn btn-primary" onClick={approveAll} style={{ display: 'flex', gap: '0.375rem', alignItems: 'center' }}>
              <CheckCheck size={15} />
              Duyệt {highValue.length} Giá Trị Cao ({fmtCurrency(totalEv)})
            </button>
          )}
          <button className="btn btn-secondary btn-sm" onClick={() => refetch()}>
            <RefreshCw size={13} />
          </button>
        </div>
      </div>

      {/* Auto-approve warning */}
      {autoApprove && (
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', padding: '0.625rem 1rem', background: 'var(--warning-muted)', border: '1px solid var(--warning)', borderRadius: 'var(--radius-sm)', fontSize: '0.8125rem' }}>
          <AlertTriangle size={14} color="var(--warning)" />
          <span style={{ color: 'var(--warning)', fontWeight: 600 }}>Tự động duyệt đang BẬT</span>
          <span style={{ color: 'var(--text-secondary)' }}>— Nội dung đủ điểm sẽ tự đăng không cần xét duyệt</span>
        </div>
      )}

      {/* ── PENDING ──────────────────────────────────────────────────────────── */}
      {pending.length === 0
        ? <EmptyState icon="✅" message="Không có nội dung nào chờ duyệt" />
        : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            <div style={{ fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--warning)' }}>
              ⏳ Chờ Quyết Định ({pending.length})
            </div>
            {pending.map(item => {
              const risk: 'low' | 'medium' | 'high' =
                item.risk_flags.length > 0 ? 'medium' : item.final_score < 0.65 ? 'medium' : 'low';
              const isHV = item.expected_value >= avgEv;
              return (
                <DecisionBlock
                  key={item.content_id}
                  badge={isHV ? 'GIÁ TRỊ CAO' : 'THƯỜNG'}
                  badgeColor={isHV ? 'var(--success)' : 'var(--text-muted)'}
                  title={`"${item.hook.slice(0, 65)}${item.hook.length > 65 ? '...' : ''}"`}
                  reason={`${item.niche} · ${item.platform} · ${item.mode} · Điểm ${Math.round(item.final_score * 100)}/100`}
                  ev={`$${item.expected_value.toFixed(2)}`}
                  confidence={`${Math.round(item.confidence * 100)}%`}
                  risk={risk}
                  riskFlags={item.risk_flags.length > 0 ? item.risk_flags : undefined}
                  ifSkip={item.risk_flags.length > 0 ? 'Có cờ rủi ro — xem xét thủ công' : 'Bỏ lỡ cơ hội doanh thu'}
                  action={{ label: '✓ Đăng Ngay', onClick: () => approve(item.content_id) }}
                  passive={{ label: 'Từ chối', onClick: () => reject(item.content_id) }}
                />
              );
            })}
          </div>
        )
      }

      {/* ── APPROVED ─────────────────────────────────────────────────────────── */}
      {approved.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <div style={{ fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--success)' }}>
            ✓ Đã Đăng ({approved.length})
          </div>
          {approved.map(item => (
            <div key={item.content_id} style={{ padding: '0.75rem 1rem', background: 'var(--surface)', border: '1px solid var(--border)', borderLeft: '4px solid var(--success)', borderRadius: 'var(--radius)', display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
              <StatusChip status={item.status} />
              <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', flex: 1 }}>{item.hook.slice(0, 70)}</span>
              <span style={{ fontSize: '0.75rem', color: 'var(--success)', fontWeight: 600 }}>${item.expected_value.toFixed(2)}</span>
              <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{item.platform} · {item.niche}</span>
            </div>
          ))}
        </div>
      )}

      {/* ── REJECTED ─────────────────────────────────────────────────────────── */}
      {rejected.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <div style={{ fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--danger)' }}>
            ✗ Đã Từ Chối ({rejected.length})
          </div>
          {rejected.map(item => (
            <div key={item.content_id} style={{ padding: '0.75rem 1rem', background: 'var(--surface)', border: '1px solid var(--border)', borderLeft: '4px solid var(--danger)', borderRadius: 'var(--radius)', display: 'flex', gap: '0.75rem', alignItems: 'center', flexWrap: 'wrap', opacity: 0.7 }}>
              <StatusChip status="rejected" />
              <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', flex: 1 }}>{item.hook.slice(0, 70)}</span>
              <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{item.platform} · {item.niche}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
