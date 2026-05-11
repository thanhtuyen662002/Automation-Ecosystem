// -- App Router — with Auth Gate -------------------------------------------
import React, { useEffect, useRef } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { AppLayout }  from '@/components/AppLayout';
import { Login }      from '@/pages/Login';
import { useAuthStore } from '@/lib/store';
import { I18nProvider } from '@/lib/i18n';
import { setUnauthorizedHandler } from '@/lib/api';

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
import { LicenseManager }     from '@/pages/LicenseManager';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchInterval: 30_000,
      retry: 2,
    },
  },
});

// AuthSync: registers the global 401 handler BEFORE children render so
// React Query never fires without it. Uses refs to avoid stale closures.
function AuthSync() {
  const { logout } = useAuthStore();
  const navigate   = useNavigate();
  const logoutRef  = useRef(logout);
  const navRef     = useRef(navigate);

  // Keep refs current every render (no stale closure)
  logoutRef.current = logout;
  navRef.current    = navigate;

  // Register synchronously on first render — before any useEffect or query fires
  setUnauthorizedHandler(() => {
    logoutRef.current();
    navRef.current('/login', { replace: true });
  });

  return null;
}

// Auth gate — redirects to /login if not authenticated
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
          {/* Register global 401 handler before any route renders */}
          <AuthSync />
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

              {/* Admin — protected by admin secret inside the page itself */}
              <Route path="admin/licenses" element={<LicenseManager />} />

              {/* Fallback → Command Center */}
              <Route path="*" element={<Navigate to="/dashboard/command" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </I18nProvider>
    </QueryClientProvider>
  );
}
