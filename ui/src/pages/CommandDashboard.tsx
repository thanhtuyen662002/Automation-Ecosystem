// ── Command Center — Real Data from GET /api/v1/system/decisions ──────────────
// PRIMARY DATA SOURCE: useDecisions() → /api/v1/system/decisions
// SECTION 1: SYSTEM alerts | SECTION 2: CONTENT decisions | SECTION 3: FLEET alerts
import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { ShieldOff, Power, RefreshCw } from 'lucide-react';
import { DecisionBlock, ConfirmDialog, StatusDot } from '@/components/ui';
import { useDecisions, useSystemStats, useFleetAccounts, useApproveContent, useRejectContent, useFreezeAccount, useSetBrainConfig } from '@/lib/hooks';
import { useUIStore, useWSStore, useAuthStore } from '@/lib/store';

type Decision = {
  id: string; type: 'system' | 'content' | 'account';
  title: string; reason: string; expected_value: number;
  confidence: number; risk_flags: string[]; action: string;
  priority_score: number; metadata: Record<string, any>;
};

export function CommandDashboard() {
  const { executionEnabled, setExecutionEnabled, autoApprove } = useUIStore();
  const { connected } = useWSStore();
  const { user, logout } = useAuthStore();
  const [confirmSafe, setConfirmSafe] = useState(false);

  // PRIMARY: decision feed from backend
  const { data: decisions = [], isLoading: decisionsLoading, error: decisionsError, refetch } = useDecisions(5);
  const { data: stats }          = useSystemStats();
  const { data: accounts = [] }  = useFleetAccounts();

  const approveM    = useApproveContent();
  const rejectM     = useRejectContent();
  const freezeM     = useFreezeAccount();
  const brainConfig = useSetBrainConfig();

  function handleAction(d: Decision) {
    switch (d.action) {
      case 'approve':         approveM.mutate(d.metadata.content_id ?? d.id); break;
      case 'freeze':          freezeM.mutate(d.metadata.account_id ?? d.id); break;
      case 'enable_execution': brainConfig.mutate({ EXECUTION_ENABLED: true }); setExecutionEnabled(true); break;
      default:                console.info('unhandled action', d.action);
    }
  }

  function handlePassive(d: Decision) {
    if (d.action === 'approve') rejectM.mutate({ id: d.metadata.content_id ?? d.id });
  }

  const systemDecisions  = (decisions as Decision[]).filter(d => d.type === 'system');
  const contentDecisions = (decisions as Decision[]).filter(d => d.type === 'content');
  const fleetDecisions   = (decisions as Decision[]).filter(d => d.type === 'account');

  const activeAccounts = (accounts as any[]).filter(a => !a.uploads_suspended && a.risk_level !== 'high').length;
  const highRiskCount  = (accounts as any[]).filter(a => a.risk_level === 'high').length;
  const pendingCount   = contentDecisions.length;

  function actionLabel(d: Decision) {
    const map: Record<string, string> = { approve: '✓ Đăng Ngay', freeze: '🔒 Đóng Băng', enable_execution: '⚡ Bật Máy Ngay', pause: '⏸ Tạm Nghỉ', monitor: '👁 Giám Sát' };
    return map[d.action] ?? d.action;
  }
  function passiveLabel(d: Decision) {
    const map: Record<string, string> = { approve: 'Từ chối', freeze: 'Chỉ Giám Sát', enable_execution: 'Để Sau', pause: 'Tiếp tục', monitor: 'Bỏ qua' };
    return map[d.action] ?? 'Bỏ qua';
  }
  function blockRisk(d: Decision): 'low' | 'medium' | 'high' {
    if (d.type === 'system' || d.risk_flags.length > 0) return 'high';
    if (d.type === 'account') return 'medium';
    return d.expected_value < 10 ? 'medium' : 'low';
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', maxWidth: 800, margin: '0 auto' }}>

      {/* ─── SECTION 1: SYSTEM ─────────────────────────────────────────────── */}
      {systemDecisions.length > 0 && (
        <div>
          <div style={{ fontSize: '0.7rem', fontWeight: 700, color: 'var(--danger)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '0.625rem' }}>
            🔴 HỆ THỐNG — Cần Hành Động Ngay
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem' }}>
            {systemDecisions.map(d => (
              <DecisionBlock key={d.id}
                badge="HỆ THỐNG" badgeColor="var(--danger)"
                title={d.title} reason={d.reason}
                risk="high"
                riskFlags={d.risk_flags.length > 0 ? d.risk_flags : undefined}
                ifSkip="Hệ thống tiếp tục đứng im — mất toàn bộ doanh thu"
                action={{ label: actionLabel(d), onClick: () => handleAction(d) }}
                passive={{ label: passiveLabel(d), onClick: undefined }}
              />
            ))}
          </div>
        </div>
      )}

      {/* ─── SECTION 2: CONTENT DECISIONS ──────────────────────────────────── */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.625rem' }}>
          <div style={{ fontSize: '0.7rem', fontWeight: 700, color: 'var(--primary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            📄 NỘI DUNG — {contentDecisions.length} đang chờ duyệt
          </div>
          <button className="btn btn-ghost btn-sm" onClick={() => refetch()} style={{ color: 'var(--text-muted)' }}>
            <RefreshCw size={12} />
          </button>
        </div>

        {decisionsLoading ? (
          <div style={{ padding: '1.5rem', textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.85rem' }}>Đang tải...</div>
        ) : decisionsError ? (
          <div style={{ padding: '1rem', background: 'var(--surface)', border: '1px solid var(--danger)', borderRadius: 'var(--radius)', fontSize: '0.8rem', color: 'var(--danger)' }}>
            ⚠ Không thể tải dữ liệu: {(decisionsError as Error).message}
            <button className="btn btn-ghost btn-sm" onClick={() => refetch()} style={{ marginLeft: '0.5rem' }}>Thử lại</button>
          </div>
        ) : contentDecisions.length === 0 ? (
          <div style={{ padding: '1.5rem', textAlign: 'center', background: 'var(--surface)', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
            <div style={{ color: 'var(--success)', fontWeight: 600 }}>✅ Không có nội dung nào cần duyệt</div>
            <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', marginTop: '0.25rem' }}>
              <Link to="/operations/queue" style={{ color: 'var(--primary)' }}>Xem toàn bộ hàng chờ →</Link>
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem' }}>
            {contentDecisions.map(d => (
              <DecisionBlock key={d.id}
                badge="NỘI DUNG" badgeColor="var(--primary)"
                title={d.title} reason={d.reason}
                ev={`$${d.expected_value.toFixed(2)}`}
                confidence={`${Math.round(d.confidence * 100)}%`}
                risk={blockRisk(d)}
                riskFlags={d.risk_flags.length > 0 ? d.risk_flags : undefined}
                ifSkip="Bỏ lỡ cơ hội doanh thu"
                action={{ label: actionLabel(d), onClick: () => handleAction(d) }}
                passive={{ label: passiveLabel(d), onClick: () => handlePassive(d) }}
              />
            ))}
            <Link to="/operations/queue" style={{ fontSize: '0.75rem', color: 'var(--primary)', textAlign: 'right', paddingRight: '0.25rem' }}>
              Xem tất cả hàng chờ →
            </Link>
          </div>
        )}
      </div>

      {/* ─── SECTION 3: FLEET / ACCOUNT ────────────────────────────────────── */}
      {fleetDecisions.length > 0 && (
        <div>
          <div style={{ fontSize: '0.7rem', fontWeight: 700, color: 'var(--danger)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '0.625rem' }}>
            ⚡ ĐỘI — {fleetDecisions.length} tài khoản cần chú ý
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem' }}>
            {fleetDecisions.map(d => (
              <DecisionBlock key={d.id}
                badge="ĐỘI" badgeColor={d.action === 'freeze' ? 'var(--danger)' : 'var(--warning)'}
                title={d.title} reason={d.reason}
                risk={blockRisk(d)}
                riskFlags={d.risk_flags.length > 0 ? d.risk_flags : undefined}
                ifSkip={d.action === 'freeze' ? 'Tài khoản có thể bị ban vĩnh viễn' : 'Tăng rủi ro bị phát hiện'}
                action={{ label: actionLabel(d), onClick: () => handleAction(d), danger: d.action === 'freeze' }}
                passive={{ label: passiveLabel(d), onClick: undefined }}
              />
            ))}
            <Link to="/fleet/health" style={{ fontSize: '0.75rem', color: 'var(--primary)', textAlign: 'right', paddingRight: '0.25rem' }}>
              Xem toàn bộ đội →
            </Link>
          </div>
        </div>
      )}

      {/* ─── STATUS BAR ────────────────────────────────────────────────────── */}
      <div>
        <h2 style={{ fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
          ⚠ TRẠNG THÁI HỆ THỐNG
        </h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '0.625rem' }}>
          <div style={{ background: 'var(--surface)', border: `1px solid ${executionEnabled ? 'var(--success)' : 'var(--danger)'}`, borderRadius: 'var(--radius)', padding: '0.875rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <StatusDot status={executionEnabled ? 'healthy' : 'failed'} />
              <span style={{ fontSize: '0.8rem', fontWeight: 600, color: executionEnabled ? 'var(--success)' : 'var(--danger)' }}>
                {executionEnabled ? 'Máy Đang Chạy' : 'Máy Đã Tắt'}
              </span>
            </div>
            <button className={`btn btn-sm ${executionEnabled ? 'btn-secondary' : 'btn-primary'}`}
              onClick={() => executionEnabled ? setConfirmSafe(true) : (brainConfig.mutate({ EXECUTION_ENABLED: true }), setExecutionEnabled(true))}>
              {executionEnabled ? <><ShieldOff size={11} /> Tắt Khẩn Cấp</> : <><Power size={11} /> Bật Lại</>}
            </button>
          </div>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '0.875rem' }}>
            <div style={{ fontSize: '1.75rem', fontWeight: 800, color: 'var(--primary)', lineHeight: 1 }}>{activeAccounts}</div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>tài khoản hoạt động</div>
            {highRiskCount > 0 && <Link to="/fleet/health" style={{ fontSize: '0.7rem', color: 'var(--danger)', display: 'block', marginTop: '0.3rem' }}>{highRiskCount} rủi ro cao →</Link>}
          </div>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '0.875rem' }}>
            <div style={{ fontSize: '1.75rem', fontWeight: 800, color: pendingCount > 0 ? 'var(--warning)' : 'var(--success)', lineHeight: 1 }}>{pendingCount}</div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>nội dung chờ duyệt</div>
            {pendingCount > 0 && <Link to="/operations/queue" style={{ fontSize: '0.7rem', color: 'var(--primary)', display: 'block', marginTop: '0.3rem' }}>Xem tất cả →</Link>}
          </div>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '0.875rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem' }}>
              <StatusDot status={connected ? 'healthy' : 'failed'} />
              <span style={{ fontSize: '0.8rem', fontWeight: 600, color: connected ? 'var(--success)' : 'var(--text-muted)' }}>{connected ? 'Live' : 'Offline'}</span>
            </div>
            {autoApprove && <div style={{ fontSize: '0.65rem', color: 'var(--warning)', marginTop: '0.3rem' }}>⚡ Tự động duyệt BẬT</div>}
          </div>
        </div>
      </div>

      {/* ─── BACKGROUND ────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: '2rem', flexWrap: 'wrap', padding: '0.875rem 1.25rem', background: 'var(--surface)', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
        {[
          { label: 'Tasks Đang Chạy', val: stats?.running ?? '—' },
          { label: 'Tasks Chờ',       val: stats?.pending ?? '—' },
          { label: 'Lỗi Hôm Nay',    val: stats?.failed  ?? '—', warn: (stats?.failed ?? 0) > 5 },
          { label: 'Operator',        val: user?.account  ?? '—' },
        ].map(({ label, val, warn }) => (
          <div key={label}>
            <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '0.1rem' }}>{label}</div>
            <div style={{ fontWeight: 700, fontSize: '0.875rem', color: (warn as boolean) ? 'var(--danger)' : 'var(--text-secondary)' }}>{val}</div>
          </div>
        ))}
        <div style={{ marginLeft: 'auto' }}>
          <button className="btn btn-ghost btn-sm" onClick={logout} style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>Đăng xuất</button>
        </div>
      </div>

      <ConfirmDialog
        open={confirmSafe}
        onClose={() => setConfirmSafe(false)}
        onConfirm={() => { brainConfig.mutate({ EXECUTION_ENABLED: false }); setExecutionEnabled(false); setConfirmSafe(false); }}
        title="Tắt Khẩn Cấp"
        message="Sẽ dừng TẤT CẢ upload và xử lý. Hệ thống vào chế độ an toàn. Bạn chắc chắn?"
        danger
      />
    </div>
  );
}
