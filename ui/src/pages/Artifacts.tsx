// ── Artifacts ─────────────────────────────────────────────────────────────────
import React, { useState } from 'react';
import { CheckCircle, XCircle, FileVideo, File } from 'lucide-react';
import { PageHeader, Badge, SectionHeader, EmptyState, ConfirmDialog } from '@/components/ui';
import { mockArtifacts } from '@/lib/mock';
import { fmtRelative } from '@/lib/utils';

export function Artifacts() {
  const [artifacts, setArtifacts] = useState(mockArtifacts);
  const [filter, setFilter] = useState<'all' | 'pending' | 'approved' | 'rejected'>('all');
  const [confirmAction, setConfirmAction] = useState<{ id: string; action: 'approved' | 'rejected' } | null>(null);

  function updateStatus(id: string, status: 'approved' | 'rejected') {
    setArtifacts(prev => prev.map(a => a.id === id ? { ...a, status } : a));
  }

  const filtered = artifacts.filter(a => filter === 'all' || a.status === filter);

  return (
    <div>
      <PageHeader title="Artifacts" subtitle="Generated media review & approval" />

      <div style={{ display: 'flex', gap: '0.375rem', marginBottom: '1.25rem', borderBottom: '1px solid var(--border)', paddingBottom: '0.75rem' }}>
        {(['all', 'pending', 'approved', 'rejected'] as const).map(f => (
          <button key={f} onClick={() => setFilter(f)} className={`btn btn-sm ${filter === f ? 'btn-primary' : 'btn-ghost'}`}>
            {f.charAt(0).toUpperCase() + f.slice(1)}
            <span style={{ marginLeft: '0.25rem', opacity: 0.7 }}>({artifacts.filter(a => f === 'all' || a.status === f).length})</span>
          </button>
        ))}
      </div>

      {filtered.length === 0
        ? <EmptyState icon="🎬" message={`No ${filter} artifacts`} />
        : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '1rem' }}>
            {filtered.map(art => (
              <div key={art.id} className="card">
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', marginBottom: '0.75rem' }}>
                  <div style={{ width: 40, height: 40, background: 'var(--surface-2)', borderRadius: 'var(--radius-sm)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--primary)', flexShrink: 0 }}>
                    {art.artifact_type === 'video' ? <FileVideo size={20} /> : <File size={20} />}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: '0.8125rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {art.storage_uri.split('/').pop()}
                    </div>
                    <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{art.artifact_type}</div>
                  </div>
                  <Badge status={art.status}>{art.status}</Badge>
                </div>

                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
                  <span>MIME: {art.mime_type ?? '—'}</span>
                  <span>Size: {art.size_bytes ? `${(art.size_bytes / 1024 / 1024).toFixed(1)} MB` : '—'}</span>
                  <span>Created: {art.created_at ? fmtRelative(new Date(art.created_at).getTime() / 1000) : '—'}</span>
                  {art.metadata.duration_sec && <span>Duration: {art.metadata.duration_sec}s</span>}
                  {art.metadata.resolution && <span>Resolution: {art.metadata.resolution as string}</span>}
                </div>

                {art.status === 'pending' && (
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button className="btn btn-primary btn-sm" style={{ flex: 1 }} onClick={() => setConfirmAction({ id: art.id, action: 'approved' })}>
                      <CheckCircle size={12} /> Approve
                    </button>
                    <button className="btn btn-danger btn-sm" style={{ flex: 1 }} onClick={() => setConfirmAction({ id: art.id, action: 'rejected' })}>
                      <XCircle size={12} /> Reject
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )
      }

      <ConfirmDialog
        open={!!confirmAction}
        onClose={() => setConfirmAction(null)}
        onConfirm={() => confirmAction && updateStatus(confirmAction.id, confirmAction.action)}
        title={`${confirmAction?.action === 'approved' ? 'Approve' : 'Reject'} Artifact`}
        message={`Mark this artifact as ${confirmAction?.action}?`}
        danger={confirmAction?.action === 'rejected'}
      />
    </div>
  );
}
