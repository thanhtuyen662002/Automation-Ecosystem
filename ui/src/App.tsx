// -- App Router — with Auth Gate -------------------------------------------
import React, { useEffect, useRef } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { AppLayout }  from '@/components/AppLayout';
import { Login }      from '@/pages/Login';
import { useAuthStore } from '@/lib/store';
import { I18nProvider } from '@/lib/i18n';
import { api, setUnauthorizedHandler, tokenStore } from '@/lib/api';

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

// AuthBootstrap — restores session on app startup via /auth/bootstrap.
//
// BUG FIX: Previously this re-ran whenever `isAuthenticated` changed, which
// caused a race condition:
//   1. User presses Login → POST /auth/login → Session A created, Token A returned
//   2. Bootstrap (still in-flight) resolves → POST /auth/bootstrap → Session B
//      created, Session A REVOKED
//   3. Frontend sends Token A → 401 "Session expired"
//
// Fix: Run ONCE on mount (empty dep array). If the user is already
// authenticated at mount time, mark bootstrap complete immediately.
// If the user logs in manually while bootstrap is in-flight, the
// `cancelled` flag discards the bootstrap result.
function AuthBootstrap() {
  const { isAuthenticated, login, setBootstrapComplete } = useAuthStore();

  useEffect(() => {
    // Already authenticated (persisted session rehydrated by zustand) — done.
    if (isAuthenticated) {
      setBootstrapComplete(true);
      return;
    }

    let cancelled = false;

    api.bootstrap()
      .then((res) => {
        // Guard: user may have logged in manually while bootstrap was in-flight.
        // Discard bootstrap result so we don't revoke their fresh session.
        if (cancelled) return;
        tokenStore.set(res.token);
        login(res.token, res.user);
      })
      .catch(() => {
        if (!cancelled) setBootstrapComplete(true);
      });

    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // ← intentionally empty: run ONCE on mount only

  return null;
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, bootstrapComplete } = useAuthStore();
  if (!bootstrapComplete) return null;
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
          <AuthBootstrap />
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
