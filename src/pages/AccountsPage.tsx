import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { HeartPulse, Link2, Link2Off, Plus, Trash2, Wifi, WifiOff } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "@/services/api";
import type { Account } from "@/types/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toast } from "@/components/ui/toast";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { useToastError } from "@/hooks/useToastError";

const PLATFORMS = ["tiktok", "youtube", "facebook"] as const;
const STATUSES = ["healthy", "limited", "banned"] as const;
const HEALTH_STATUSES = ["healthy", "limited", "banned"] as const;

const PLATFORM_ICON: Record<string, string> = {
  tiktok: "🎵",
  youtube: "▶️",
  facebook: "📘",
};

const STATUS_VARIANT: Record<string, "success" | "failed" | "pending" | "retry" | "running" | "default"> = {
  healthy: "success",
  limited: "retry",
  banned: "failed",
};

// ── Session Status Badge ──────────────────────────────────────────────────────

function SessionBadge({ account }: { account: Account }) {
  if (account.session_valid) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs font-medium text-emerald-600 dark:text-emerald-400">
        <Wifi className="h-3 w-3" />
        Connected
      </span>
    );
  }
  if (account.last_login_at) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-xs font-medium text-amber-600 dark:text-amber-400">
        <WifiOff className="h-3 w-3" />
        Expired
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
      <Link2Off className="h-3 w-3" />
      Not Connected
    </span>
  );
}

export function AccountsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  // ── Filters ────────────────────────────────────────────────────────────────
  const [filterPlatform, setFilterPlatform] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);

  // ── Add form ───────────────────────────────────────────────────────────────
  const [platform, setPlatform] = useState<string>("tiktok");
  const [accountHandle, setAccountHandle] = useState("");
  const [proxyUrl, setProxyUrl] = useState("");

  // ── Dialogs ────────────────────────────────────────────────────────────────
  const [deleteTarget, setDeleteTarget] = useState<Account | null>(null);
  const [healthTarget, setHealthTarget] = useState<Account | null>(null);
  const [healthStatus, setHealthStatus] = useState<string>("healthy");
  const [connectingId, setConnectingId] = useState<string | null>(null);

  // ── Queries ────────────────────────────────────────────────────────────────
  const accountsQuery = useQuery({
    queryKey: ["accounts"],
    queryFn: () => api.getAccounts({ limit: 200 }),
    refetchInterval: autoRefresh ? 8000 : false,
  });

  const accounts: Account[] = (accountsQuery.data?.items ?? []).filter((a) => {
    if (filterPlatform && a.platform !== filterPlatform) return false;
    if (filterStatus && a.status !== filterStatus) return false;
    return true;
  });

  useToastError(accountsQuery.error, t("accounts.errorLoad"));

  // ── Mutations ──────────────────────────────────────────────────────────────
  const createMutation = useMutation({
    mutationFn: () =>
      api.createAccount({ platform, account_handle: accountHandle, proxy_url: proxyUrl || undefined }),
    onSuccess: async (newAccount) => {
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
      setAccountHandle("");
      setProxyUrl("");
      toast({ title: t("accounts.accountAdded"), description: t("accounts.accountAddedDesc") });

      // ── Auto-create default posting policy ────────────────────────────────
      // New accounts get a sensible default: 10 posts/day, 5-minute cooldown.
      // This runs silently — a failure here is non-fatal.
      const ACTION_MAP: Record<string, string> = {
        tiktok: "publish_tiktok",
        youtube: "publish_youtube",
        facebook: "publish_facebook",
      };
      const actionType = ACTION_MAP[platform];
      if (actionType) {
        try {
          await api.createPolicyRule({
            action_type: actionType,
            rule_name: `${platform}-default-${Date.now()}`,
            max_actions: 10,
            window_seconds: 86400, // 1 day
            cooldown_seconds: 300, // 5 minutes
          });
          queryClient.invalidateQueries({ queryKey: ["policy-rules"] });
        } catch {
          // Non-fatal: rule may already exist or policy API unavailable
          console.warn("Auto policy creation failed — user can configure manually");
        }
      }
    },
    onError: () => toast({ title: t("accounts.errorCreate"), variant: "destructive" }),
  });


  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteAccount(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
      setDeleteTarget(null);
      toast({ title: t("accounts.accountDeleted"), description: t("accounts.accountDeletedDesc") });
    },
    onError: () => toast({ title: t("accounts.errorDelete"), variant: "destructive" }),
  });

  const healthMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => api.updateAccountHealth(id, status),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
      setHealthTarget(null);
      toast({ title: t("accounts.statusUpdated"), description: t("accounts.statusUpdatedDesc") });
    },
    onError: () => toast({ title: t("accounts.errorHealthCheck"), variant: "destructive" }),
  });

  const connectMutation = useMutation({
    mutationFn: (id: string) => api.connectAccount(id),
    onMutate: (id) => {
      setConnectingId(id);
      toast({
        title: "Browser opening…",
        description: "A browser window will open. Log in manually, then come back here.",
      });
    },
    onSuccess: (updatedAccount) => {
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
      setConnectingId(null);
      if (updatedAccount.session_valid) {
        toast({
          title: "✅ Account Connected",
          description: `${updatedAccount.account_handle} is now ready to publish.`,
        });
      }
    },
    onError: (err: unknown) => {
      setConnectingId(null);
      const msg = err instanceof Error ? err.message : "Unknown error";
      toast({
        title: "Connection Failed",
        description: msg.includes("408") ? "Login timed out. Please try again." : msg,
        variant: "destructive",
      });
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader title={t("accounts.title")} description={t("accounts.description")} />

      {/* ── Add account form ──────────────────────────────────────────────── */}
      <Card>
        <CardContent className="p-5">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground">{t("accounts.platform")}</label>
              <Select value={platform} onChange={(e) => setPlatform(e.target.value)}>
                {PLATFORMS.map((p) => (
                  <option key={p} value={p}>
                    {PLATFORM_ICON[p]} {p.charAt(0).toUpperCase() + p.slice(1)}
                  </option>
                ))}
              </Select>
            </div>
            <div className="flex flex-col gap-1 grow min-w-[180px]">
              <label className="text-xs font-medium text-muted-foreground">{t("accounts.handle")}</label>
              <Input
                placeholder={t("accounts.placeholderHandle")}
                value={accountHandle}
                onChange={(e) => setAccountHandle(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1 grow min-w-[200px]">
              <label className="text-xs font-medium text-muted-foreground">{t("accounts.proxyUrl")}</label>
              <Input
                placeholder={t("accounts.placeholderProxy")}
                value={proxyUrl}
                onChange={(e) => setProxyUrl(e.target.value)}
              />
            </div>
            <Button
              disabled={!accountHandle.trim() || createMutation.isPending}
              onClick={() => createMutation.mutate()}
            >
              <Plus className="h-4 w-4" />
              {createMutation.isPending ? t("accounts.adding") : t("accounts.addBtn")}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* ── Info banner: how Connect works ────────────────────────────────── */}
      <div className="flex items-start gap-3 rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm dark:border-blue-800 dark:bg-blue-950/30">
        <Link2 className="mt-0.5 h-4 w-4 shrink-0 text-blue-500" />
        <div>
          <p className="font-medium text-blue-900 dark:text-blue-200">How "Connect" works</p>
          <p className="mt-0.5 text-blue-700 dark:text-blue-400">
            Clicking Connect will open a real browser window. Log in to the platform normally — no credentials
            are stored or automated. Once logged in, your session cookies are saved securely for publishing.
          </p>
        </div>
      </div>

      {/* ── Filters ───────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap gap-3">
        <Select value={filterPlatform} onChange={(e) => setFilterPlatform(e.target.value)}>
          <option value="">{t("accounts.filterPlatform")}</option>
          {PLATFORMS.map((p) => (
            <option key={p} value={p}>
              {PLATFORM_ICON[p]} {p.charAt(0).toUpperCase() + p.slice(1)}
            </option>
          ))}
        </Select>
        <Select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
          <option value="">{t("accounts.filterStatus")}</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </Select>
        <label className="inline-flex h-9 items-center gap-2 rounded-md border bg-card px-3 text-sm">
          <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
          {t("accounts.autoRefresh")}
        </label>
      </div>

      {/* ── Table ─────────────────────────────────────────────────────────── */}
      <Card>
        <CardContent className="p-0">
          {accountsQuery.isLoading ? (
            <div className="space-y-3 p-5">
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
            </div>
          ) : accounts.length === 0 ? (
            <EmptyState title={t("accounts.noAccounts")} description={t("accounts.noAccountsDesc")} />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("accounts.platform")}</TableHead>
                  <TableHead>{t("accounts.handle")}</TableHead>
                  <TableHead>{t("accounts.status")}</TableHead>
                  <TableHead>Session</TableHead>
                  <TableHead>{t("accounts.proxyUrl")}</TableHead>
                  <TableHead className="text-right">{t("accounts.actions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {accounts.map((acc) => {
                  const isConnecting = connectingId === acc.id;
                  return (
                    <TableRow key={acc.id}>
                      <TableCell className="font-medium">
                        <span className="mr-1">{PLATFORM_ICON[acc.platform] ?? "🌐"}</span>
                        <span className="capitalize">{acc.platform}</span>
                      </TableCell>
                      <TableCell className="font-mono text-sm">{acc.account_handle}</TableCell>
                      <TableCell>
                        <Badge variant={STATUS_VARIANT[acc.status] ?? "default"}>{acc.status.toUpperCase()}</Badge>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-col gap-1">
                          <SessionBadge account={acc} />
                          {acc.last_login_at && (
                            <span className="text-xs text-muted-foreground">
                              {new Date(acc.last_login_at).toLocaleDateString()}
                            </span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground max-w-[160px] truncate">
                        {acc.proxy_url ?? "—"}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="inline-flex gap-2">
                          {/* Connect / Reconnect button */}
                          <Button
                            variant={acc.session_valid ? "outline" : "default"}
                            size="sm"
                            disabled={isConnecting}
                            onClick={() => connectMutation.mutate(acc.id)}
                            className={acc.session_valid ? "" : "gap-1.5"}
                          >
                            {isConnecting ? (
                              <>
                                <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                                Waiting…
                              </>
                            ) : acc.session_valid ? (
                              <>
                                <Link2 className="h-3.5 w-3.5" />
                                Reconnect
                              </>
                            ) : (
                              <>
                                <Link2 className="h-3.5 w-3.5" />
                                Connect
                              </>
                            )}
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => { setHealthTarget(acc); setHealthStatus(acc.status); }}
                          >
                            <HeartPulse className="h-3.5 w-3.5" />
                            {t("accounts.healthCheck")}
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setDeleteTarget(acc)}
                          >
                            <Trash2 className="h-3.5 w-3.5 text-destructive" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* ── Delete dialog ─────────────────────────────────────────────────── */}
      <ConfirmDialog
        open={deleteTarget !== null}
        title={t("accounts.deleteConfirmTitle")}
        description={`${t("accounts.deleteConfirmDesc")} (${deleteTarget?.account_handle})`}
        confirmLabel={t("accounts.deleteConfirm")}
        loading={deleteMutation.isPending}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
      />

      {/* ── Health check dialog ───────────────────────────────────────────── */}
      <ConfirmDialog
        open={healthTarget !== null}
        title={t("accounts.healthCheckTitle")}
        description={
          <div className="space-y-3 pt-1">
            <p className="text-sm text-muted-foreground">{t("accounts.healthCheckDesc")}</p>
            <Select value={healthStatus} onChange={(e) => setHealthStatus(e.target.value)}>
              {HEALTH_STATUSES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </Select>
          </div>
        }
        confirmLabel={t("accounts.healthCheckConfirm")}
        loading={healthMutation.isPending}
        onClose={() => setHealthTarget(null)}
        onConfirm={() => healthTarget && healthMutation.mutate({ id: healthTarget.id, status: healthStatus })}
      />
    </div>
  );
}
