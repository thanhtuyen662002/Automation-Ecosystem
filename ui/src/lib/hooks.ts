// ── React Query Hooks — Real API Only ────────────────────────────────────────
// NO mock data. NO USE_MOCK_FALLBACK.
// On API error → React Query surfaces error → UI renders <ErrorState>.
// Consumers MUST handle { data, isLoading, error } from every hook.

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from './api';
import {
  artifactsRefetchInterval,
  DASHBOARD_ANALYTICS_REFETCH_MS,
  jobsRefetchInterval,
  LIVE_STATS_REFETCH_MS,
  queueRefetchInterval,
} from './polling';

// ── Content Queue ─────────────────────────────────────────────────────────────
export function useQueue() {
  return useQuery({
    queryKey: ['queue'],
    queryFn: () => api.queue('all'),
    staleTime: 1_000,
    refetchInterval: (query) => queueRefetchInterval(query.state.data),
    refetchIntervalInBackground: true,
  });
}

// ── Accounts ──────────────────────────────────────────────────────────────────
export function useAccounts() {
  return useQuery({
    queryKey: ['accounts'],
    queryFn: api.accounts,
    staleTime: 20_000,
    refetchInterval: 30_000,
  });
}

export function useCreateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: { platform: string; account_handle: string; profile_url?: string; proxy_url?: string; metadata?: Record<string, unknown>; browser_provider?: string; adspower_profile_id?: string }) =>
      api.createAccount(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}

export function useUpdateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: { account_handle?: string; profile_url?: string | null; proxy_url?: string | null; metadata?: Record<string, unknown>; browser_provider?: string | null; real_chrome_user_data_dir?: string | null; adspower_profile_id?: string | null } }) =>
      api.updateAccount(id, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}

export function useDeleteAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteAccount(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}

export function useMarkSoftBan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.markSoftBan(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}

export function useClearSoftBan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.clearSoftBan(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}

// Opens a real browser for manual login (long-running — up to 5 min).
// Invalidates accounts list on success so avatar/display_name appears immediately.
export function useConnectAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.connectAccount(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}

export function useConfirmManualLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.confirmManualLogin(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}

// ── Fleet Health ──────────────────────────────────────────────────────────────
export function useFleet() {
  return useQuery({
    queryKey: ['fleet'],
    queryFn: api.fleet,
    staleTime: 10_000,
    refetchInterval: 15_000,
  });
}

// Extracts accounts array from FleetHealthResponse.accounts
export function useFleetAccounts() {
  return useQuery({
    queryKey: ['fleet', 'accounts'],
    queryFn: () => api.fleet().then(d => d.accounts ?? []),
    staleTime: 10_000,
    refetchInterval: 15_000,
  });
}

// ── System Stats ──────────────────────────────────────────────────────────────
export function useSystemStats() {
  return useQuery({
    queryKey: ['stats'],
    queryFn: api.stats,
    staleTime: 2_000,
    refetchInterval: LIVE_STATS_REFETCH_MS,
    refetchIntervalInBackground: true,
  });
}

// ── System Deep Health ────────────────────────────────────────────────────────
export function useDeepHealth() {
  return useQuery({
    queryKey: ['deepHealth'],
    queryFn: api.deepHealth,
    staleTime: 5_000,
    refetchInterval: 10_000,
    refetchIntervalInBackground: true,
  });
}

// ── Decision Feed (Command Dashboard primary data source) ─────────────────────
export function useDecisions(limit = 5) {
  return useQuery({
    queryKey: ['decisions', limit],
    queryFn: () => api.decisions(limit),
    staleTime: 2_000,
    refetchInterval: LIVE_STATS_REFETCH_MS,
    refetchIntervalInBackground: true,
  });
}

// ── Strategy ──────────────────────────────────────────────────────────────────
export function useStrategy() {
  return useQuery({
    queryKey: ['strategy'],
    queryFn: api.strategy,
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
}

export function useUpdateStrategy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: object) => api.updateStrategy(patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategy'] }),
  });
}

export function useNiches() {
  return useQuery({
    queryKey: ['niches'],
    queryFn: api.niches,
    staleTime: 60_000,
    refetchInterval: 60_000,
  });
}

export function useRecommendations() {
  return useQuery({
    queryKey: ['recommendations'],
    queryFn: api.recommendations,
    staleTime: 60_000,
    refetchInterval: 60_000,
  });
}

export function useStrategyLog(limit = 50) {
  return useQuery({
    queryKey: ['strategyLog', limit],
    queryFn: () => api.strategyLog(limit),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
}

export function useOverrides() {
  return useQuery({
    queryKey: ['overrides'],
    queryFn: api.overrides,
    staleTime: 15_000,
    refetchInterval: 15_000,
  });
}

// ── Policy Rules ──────────────────────────────────────────────────────────────
export function usePolicyRules() {
  return useQuery({
    queryKey: ['policyRules'],
    queryFn: api.policyRules,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

export function useCreatePolicyRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: object) => api.createPolicyRule(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['policyRules'] }),
  });
}

export function useTogglePolicyRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.togglePolicyRule(id, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['policyRules'] }),
  });
}

export function useDeletePolicyRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deletePolicyRule(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['policyRules'] }),
  });
}

// AI Provider Settings
export function useAiProviders() {
  return useQuery({
    queryKey: ['aiProviders'],
    queryFn: api.aiProviders,
    staleTime: 10_000,
    refetchInterval: 30_000,
  });
}

export function useCreateAiProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.createAiProvider,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['aiProviders'] }),
  });
}

export function useUpdateAiProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Parameters<typeof api.updateAiProvider>[1] }) =>
      api.updateAiProvider(id, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['aiProviders'] }),
  });
}

export function useDeleteAiProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.deleteAiProvider,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['aiProviders'] }),
  });
}

export function useCreateAiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ providerId, payload }: { providerId: string; payload: Parameters<typeof api.createAiKey>[1] }) =>
      api.createAiKey(providerId, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['aiProviders'] }),
  });
}

export function useUpdateAiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ keyId, payload }: { keyId: string; payload: Parameters<typeof api.updateAiKey>[1] }) =>
      api.updateAiKey(keyId, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['aiProviders'] }),
  });
}

export function useDeleteAiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.deleteAiKey,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['aiProviders'] }),
  });
}

export function useCreateAiModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ providerId, payload }: { providerId: string; payload: Parameters<typeof api.createAiModel>[1] }) =>
      api.createAiModel(providerId, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['aiProviders'] }),
  });
}

export function useUpdateAiModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ modelId, payload }: { modelId: string; payload: Parameters<typeof api.updateAiModel>[1] }) =>
      api.updateAiModel(modelId, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['aiProviders'] }),
  });
}

export function useDeleteAiModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.deleteAiModel,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['aiProviders'] }),
  });
}

export function useTestAiProvider() {
  return useMutation({
    mutationFn: api.testAiProvider,
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
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['fleet'] });
      qc.invalidateQueries({ queryKey: ['overrides'] });
    },
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

// ── Artifacts ─────────────────────────────────────────────────────────────────
export function useArtifacts(limit = 50) {
  return useQuery({
    queryKey: ['artifacts', limit],
    queryFn: () => api.artifacts(limit),
    staleTime: 1_000,
    refetchInterval: (query) => artifactsRefetchInterval(query.state.data),
    refetchIntervalInBackground: true,
  });
}

export function useUpdateArtifactStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: 'approved' | 'rejected' }) =>
      api.updateArtifactStatus(id, status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['artifacts'] }),
  });
}

// ── Jobs / Pipeline ───────────────────────────────────────────────────────────
export function useJobs() {
  return useQuery({
    queryKey: ['jobs'],
    queryFn: api.jobs,
    staleTime: 1_000,
    refetchInterval: (query) => jobsRefetchInterval(query.state.data),
    refetchIntervalInBackground: true,
  });
}

export function useLaunchPipeline() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: { product_url?: string; product_image_path?: string; top_n?: number; priority?: number; account_id: string; auto_publish?: boolean }) =>
      api.launchPipeline(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['jobs'] }),
  });
}

export function useDeleteJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteJob(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['jobs'] }),
  });
}

// ── Niche Upsert ──────────────────────────────────────────────────────────────
export function useUploadProductImage() {
  return useMutation({
    mutationFn: (file: File) => api.uploadProductImage(file),
  });
}

export function useUpsertNiche() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      niche: string; platform: string; win_rate: number;
      avg_views: number; avg_revenue: number; posts_count: number; growth_potential: number;
    }) => api.upsertNiche(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['niches'] }),
  });
}

// ── Brain Config ──────────────────────────────────────────────────────────────
export function useBrainConfig() {
  return useQuery({
    queryKey: ['brainConfig'],
    queryFn: api.brainConfig,
    staleTime: 60_000,
    refetchInterval: 60_000,
  });
}

// ── Analytics Overview ────────────────────────────────────────────────────────
export function useAnalyticsOverview() {
  return useQuery({
    queryKey: ['analyticsOverview'],
    queryFn: api.analyticsOverview,
    staleTime: 15_000,
    refetchInterval: DASHBOARD_ANALYTICS_REFETCH_MS,
  });
}
