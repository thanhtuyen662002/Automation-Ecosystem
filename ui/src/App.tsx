import React, { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { AppLayout } from '@/components/AppLayout';
import { Login } from '@/pages/Login';
import { I18nProvider } from '@/lib/i18n';
import { api } from '@/lib/api';
import type { LicenseResponse } from '@/types/license';

import { ExecutiveDashboard } from '@/pages/ExecutiveDashboard';
import { CommandDashboard } from '@/pages/CommandDashboard';
import { ContentQueue } from '@/pages/ContentQueue';
import { PipelineJobs } from '@/pages/PipelineJobs';
import { Artifacts } from '@/pages/Artifacts';
import { FleetHealth } from '@/pages/FleetHealth';
import { Accounts } from '@/pages/Accounts';
import { Identities } from '@/pages/Identities';
import { CeoBrain } from '@/pages/CeoBrain';
import { NichePerformance } from '@/pages/NichePerformance';
import { Overrides } from '@/pages/Overrides';
import { SettingsGeneral } from '@/pages/SettingsGeneral';
import { SettingsAdvanced } from '@/pages/SettingsAdvanced';
import { SettingsPolicy } from '@/pages/SettingsPolicy';
import { SettingsAI } from '@/pages/SettingsAI';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchInterval: 30_000,
      retry: 2,
    },
  },
});

function LoadingLicense() {
  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: 'var(--bg)', color: 'var(--text-secondary)' }}>
      Đang kiểm tra license…
    </div>
  );
}

function LicenseGate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<LicenseResponse | null>(null);
  const [loading, setLoading] = useState(true);

  async function loadStatus() {
    setLoading(true);
    try {
      const response = await api.licenseStatus();
      setStatus(response);
    } catch {
      setStatus({
        ok: false,
        licensed: false,
        status: 'server_error',
        reason: 'Không thể kết nối backend local để kiểm tra license.',
        license: null,
        device: null,
        offline_valid_until: null,
      });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadStatus();
  }, []);

  if (loading && status === null) return <LoadingLicense />;
  if (status?.licensed && (status.status === 'active' || status.status === 'active_offline')) return <>{children}</>;
  return <Login status={status} onLicenseChanged={loadStatus} />;
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Navigate to="/" replace />} />
            <Route path="/" element={<LicenseGate><AppLayout /></LicenseGate>}>
              <Route index element={<Navigate to="/dashboard/command" replace />} />
              <Route path="dashboard/command" element={<CommandDashboard />} />
              <Route path="dashboard/executive" element={<ExecutiveDashboard />} />
              <Route path="operations/queue" element={<ContentQueue />} />
              <Route path="operations/jobs" element={<PipelineJobs />} />
              <Route path="operations/artifacts" element={<Artifacts />} />
              <Route path="fleet/health" element={<FleetHealth />} />
              <Route path="fleet/accounts" element={<Accounts />} />
              <Route path="fleet/identities" element={<Identities />} />
              <Route path="strategy/ceo" element={<CeoBrain />} />
              <Route path="strategy/niches" element={<NichePerformance />} />
              <Route path="strategy/overrides" element={<Overrides />} />
              <Route path="settings/general" element={<SettingsGeneral />} />
              <Route path="settings/advanced" element={<SettingsAdvanced />} />
              <Route path="settings/policy" element={<SettingsPolicy />} />
              <Route path="settings/ai" element={<SettingsAI />} />
              <Route path="*" element={<Navigate to="/dashboard/command" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </I18nProvider>
    </QueryClientProvider>
  );
}
