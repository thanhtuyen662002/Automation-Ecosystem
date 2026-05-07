import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Shield, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "@/services/api";
import type { PolicyRule } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { useToastError } from "@/hooks/useToastError";

// ── Platform → action_type mapping (hidden from user) ────────────────────────
const PLATFORM_TO_ACTION: Record<string, string> = {
  tiktok: "publish_tiktok",
  youtube: "publish_youtube",
  facebook: "publish_facebook",
};

const PLATFORM_LABELS: Record<string, { icon: string; label: string }> = {
  tiktok: { icon: "🎵", label: "TikTok" },
  youtube: { icon: "▶️", label: "YouTube" },
  facebook: { icon: "📘", label: "Facebook" },
};

function actionTypeToPlatform(actionType: string): string {
  return actionType.replace("publish_", "");
}

export function PostingLimitsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  // ── Form state (user-friendly) ─────────────────────────────────────────────
  const [platform, setPlatform] = useState("tiktok");
  const [maxPerDay, setMaxPerDay] = useState("10");
  const [cooldownMinutes, setCooldownMinutes] = useState("5");

  // ── Delete dialog ──────────────────────────────────────────────────────────
  const [deleteTarget, setDeleteTarget] = useState<PolicyRule | null>(null);

  // ── Query ──────────────────────────────────────────────────────────────────
  const rulesQuery = useQuery({
    queryKey: ["policy-rules"],
    queryFn: () => api.getPolicyRules({ limit: 200 }),
    refetchInterval: 15000,
  });

  const rules: PolicyRule[] = rulesQuery.data?.items ?? [];
  useToastError(rulesQuery.error, t("postingLimits.errorLoad"));

  // ── Create mutation ────────────────────────────────────────────────────────
  const createMutation = useMutation({
    mutationFn: () =>
      api.createPolicyRule({
        action_type: PLATFORM_TO_ACTION[platform],
        rule_name: `${platform}-limit-${Date.now()}`,
        max_actions: Number(maxPerDay),
        window_seconds: 86400,              // 1 day — hidden from user
        cooldown_seconds: Number(cooldownMinutes) * 60,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["policy-rules"] });
      toast({ title: t("postingLimits.limitAdded"), description: t("postingLimits.limitAddedDesc") });
    },
    onError: () => toast({ title: t("postingLimits.errorCreate"), variant: "destructive" }),
  });

  // ── Delete mutation ────────────────────────────────────────────────────────
  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deletePolicyRule(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["policy-rules"] });
      setDeleteTarget(null);
      toast({ title: t("postingLimits.limitDeleted"), description: t("postingLimits.limitDeletedDesc") });
    },
    onError: () => toast({ title: t("postingLimits.errorDelete"), variant: "destructive" }),
  });

  const canCreate =
    Number(maxPerDay) >= 1 && Number(cooldownMinutes) >= 0 && !createMutation.isPending;

  return (
    <div className="space-y-6">
      <PageHeader title={t("postingLimits.title")} description={t("postingLimits.description")} />

      {/* ── Add limit form ────────────────────────────────────────────────── */}
      <Card>
        <CardContent className="p-6">
          <h3 className="mb-1 font-semibold">{t("postingLimits.addLimit")}</h3>
          <p className="mb-4 text-sm text-muted-foreground">
            Set how often each platform can post per day and how long to wait between posts.
          </p>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4 items-end">
            {/* Platform */}
            <div className="flex flex-col gap-1.5">
              <label className="text-sm font-medium">{t("postingLimits.platform")}</label>
              <Select value={platform} onChange={(e) => setPlatform(e.target.value)}>
                {Object.entries(PLATFORM_LABELS).map(([key, { icon, label }]) => (
                  <option key={key} value={key}>
                    {icon} {label}
                  </option>
                ))}
              </Select>
            </div>

            {/* Max per day */}
            <div className="flex flex-col gap-1.5">
              <label className="text-sm font-medium">{t("postingLimits.maxPerDay")}</label>
              <Input
                type="number"
                min={1}
                max={100}
                value={maxPerDay}
                onChange={(e) => setMaxPerDay(e.target.value)}
              />
            </div>

            {/* Cooldown */}
            <div className="flex flex-col gap-1.5">
              <label className="text-sm font-medium">{t("postingLimits.cooldownMinutes")}</label>
              <Input
                type="number"
                min={0}
                max={1440}
                value={cooldownMinutes}
                onChange={(e) => setCooldownMinutes(e.target.value)}
              />
            </div>

            {/* Submit */}
            <Button disabled={!canCreate} onClick={() => createMutation.mutate()}>
              <Plus className="h-4 w-4" />
              {t("postingLimits.addLimit")}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* ── Active limits ─────────────────────────────────────────────────── */}
      <div>
        <h2 className="mb-3 font-semibold text-sm text-muted-foreground uppercase tracking-wider">
          Active Limits
        </h2>
        {rulesQuery.isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-20" />
            <Skeleton className="h-20" />
          </div>
        ) : rules.length === 0 ? (
          <EmptyState title={t("postingLimits.noLimits")} description={t("postingLimits.noLimitsDesc")} />
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {rules.map((rule) => {
              const plt = actionTypeToPlatform(rule.action_type);
              const meta = PLATFORM_LABELS[plt] ?? { icon: "🌐", label: plt };
              const cooldownMin = Math.round(rule.cooldown_seconds / 60);

              return (
                <Card key={rule.id} className="border transition-all hover:shadow-sm">
                  <CardContent className="p-4">
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="text-2xl">{meta.icon}</span>
                        <div>
                          <div className="font-semibold text-sm">{meta.label}</div>
                          <div className="text-xs text-muted-foreground">
                            {rule.max_actions ?? "∞"} posts/day
                          </div>
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-muted-foreground hover:text-destructive"
                        onClick={() => setDeleteTarget(rule)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2 text-xs">
                      <span className="flex items-center gap-1 rounded-full bg-muted px-2 py-0.5">
                        <Shield className="h-3 w-3" />
                        {cooldownMin} min cooldown
                      </span>
                      <span className="flex items-center gap-1 rounded-full bg-muted px-2 py-0.5">
                        {t("postingLimits.appliesToGlobal")}
                      </span>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Delete confirm ────────────────────────────────────────────────── */}
      <ConfirmDialog
        open={deleteTarget !== null}
        title={t("postingLimits.deleteConfirmTitle")}
        description={t("postingLimits.deleteConfirmDesc")}
        confirmLabel={t("postingLimits.deleteConfirm")}
        loading={deleteMutation.isPending}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
      />
    </div>
  );
}
