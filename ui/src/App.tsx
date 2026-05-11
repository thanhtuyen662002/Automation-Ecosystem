// ── App Router — with Auth Gate ───────────────────────────────────────────────
import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { AppLayout }  from '@/components/AppLayout';
import { Login }      from '@/pages/Login';
import { useAuthStore } from '@/lib/store';
import { I18nProvider } from '@/lib/i18n';

// Pages
import { ExecutiveDashboard } from '@/pages/ExecutiveDashboard';
import { CommandDashboard }   from '@/pages/CommandDashboard';
import { ContentQueue }       from '@/pages/ContentQueue';
import { PipelineJobs }       from '@/pages/PipelineJobs';
import { Artifacts }          from '@/pages/Artifacts';
import { FleetHealth }        from '@/pages/FleetHealth';
import { Accounts }           from '@/pages/Accounts';
import { Identities }         from '@/pages/Identities';
import { CeoBrain }           from '@/pages/CeoBrain';
import { NichePerformance }   from '@/pages/NichePerformance';
import { Overrides }          from '@/pages/Overrides';
import { SettingsGeneral }    from '@/pages/SettingsGeneral';
import { SettingsAdvanced }   from '@/pages/SettingsAdvanced';
import { SettingsPolicy }     from '@/pages/SettingsPolicy';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchInterval: 30_000,
      retry: 2,
    },
  },
});

// Auth guard — redirects to /login if not authenticated
function RequireAuth({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuthStore();
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <BrowserRouter>
          <Routes>
            {/* Public */}
            <Route path="/login" element={<Login />} />

            {/* Protected */}
            <Route path="/" element={<RequireAuth><AppLayout /></RequireAuth>}>
              {/* Default: Command Center is P0 — most important page */}
              <Route index element={<Navigate to="/dashboard/command" replace />} />

              {/* Dashboards */}
              <Route path="dashboard/command"   element={<CommandDashboard />} />
              <Route path="dashboard/executive" element={<ExecutiveDashboard />} />

              {/* Operations */}
              <Route path="operations/queue"     element={<ContentQueue />} />
              <Route path="operations/jobs"      element={<PipelineJobs />} />
              <Route path="operations/artifacts" element={<Artifacts />} />

              {/* Fleet */}
              <Route path="fleet/health"     element={<FleetHealth />} />
              <Route path="fleet/accounts"   element={<Accounts />} />
              <Route path="fleet/identities" element={<Identities />} />

              {/* Strategy */}
              <Route path="strategy/ceo"       element={<CeoBrain />} />
              <Route path="strategy/niches"    element={<NichePerformance />} />
              <Route path="strategy/overrides" element={<Overrides />} />

              {/* Settings */}
              <Route path="settings/general"  element={<SettingsGeneral />} />
              <Route path="settings/advanced" element={<SettingsAdvanced />} />
              <Route path="settings/policy"   element={<SettingsPolicy />} />

              {/* Fallback → Command Center */}
              <Route path="*" element={<Navigate to="/dashboard/command" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </I18nProvider>
    </QueryClientProvider>
  );
}
