// ── React Query Hooks — Real API Only ────────────────────────────────────────
// NO mock data. NO USE_MOCK_FALLBACK.
// On API error → React Query surfaces error → UI renders <ErrorState>.
// Consumers MUST handle { data, isLoading, error } from every hook.

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from './api';

// ── Content Queue ─────────────────────────────────────────────────────────────
// Fetches ALL statuses in one call (status=all) so ContentQueue can
// show pending/approved/rejected without 3 round trips.
export function useQueue() {
  return useQuery({
    queryKey: ['queue'],
    queryFn: () => api.queue('all'),
    staleTime: 15_000,
    refetchInterval: 15_000,
  });
}

// ── Fleet Health ──────────────────────────────────────────────────────────────
export function useFleet() {
  return useQuery({
    queryKey: ['fleet'],
    queryFn: api.fleet,
    staleTime: 20_000,
    refetchInterval: 20_000,
  });
}

// Extracts accounts array from FleetHealthResponse.accounts
export function useFleetAccounts() {
  return useQuery({
    queryKey: ['fleet', 'accounts'],
    queryFn: () => api.fleet().then(d => d.accounts ?? []),
    staleTime: 20_000,
    refetchInterval: 20_000,
  });
}

// ── System Stats ──────────────────────────────────────────────────────────────
export function useSystemStats() {
  return useQuery({
    queryKey: ['stats'],
    queryFn: api.stats,
    staleTime: 10_000,
    refetchInterval: 10_000,
  });
}

// ── Decision Feed (Command Dashboard primary data source) ─────────────────────
export function useDecisions(limit = 5) {
  return useQuery({
    queryKey: ['decisions', limit],
    queryFn: () => api.decisions(limit),
    staleTime: 10_000,
    refetchInterval: 10_000,
  });
}

// ── Strategy ──────────────────────────────────────────────────────────────────
export function useStrategy() {
  return useQuery({
    queryKey: ['strategy'],
    queryFn: api.strategy,
    staleTime: 30_000,
  });
}

export function useNiches() {
  return useQuery({
    queryKey: ['niches'],
    queryFn: api.niches,
    staleTime: 60_000,
  });
}

export function useOverrides() {
  return useQuery({
    queryKey: ['overrides'],
    queryFn: api.overrides,
    staleTime: 15_000,
  });
}

// ── Mutations ─────────────────────────────────────────────────────────────────
export function useApproveContent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.approveContent(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['queue'] }),
  });
}

export function useRejectContent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
      api.rejectContent(id, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['queue'] }),
  });
}

export function useFreezeAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.freezeAccount(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['fleet'] }),
  });
}

export function useClearCooldown() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.clearCooldown(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['fleet'] }),
  });
}

export function useSetBrainConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Record<string, unknown>) => api.setBrainConfig(patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['decisions'] });
      qc.invalidateQueries({ queryKey: ['queue'] });
    },
  });
}

export function useAddOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: object) => api.addOverride(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['overrides'] }),
  });
}

export function useRemoveOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.removeOverride(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['overrides'] }),
  });
}
