import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, XCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "@/services/api";
import type { Artifact, ArtifactStatus } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { useToastError } from "@/hooks/useToastError";
import { cn } from "@/lib/utils";

const STATUS_LABELS: Record<ArtifactStatus, string> = {
  pending: "Awaiting Review",
  approved: "Approved",
  rejected: "Rejected",
};

const STATUS_BADGE: Record<ArtifactStatus, string> = {
  pending: "bg-amber-100 text-amber-800 border-amber-200 dark:bg-amber-900/30 dark:text-amber-300",
  approved: "bg-emerald-100 text-emerald-800 border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-300",
  rejected: "bg-red-100 text-red-800 border-red-200 dark:bg-red-900/30 dark:text-red-300",
};

const FILTER_OPTIONS: Array<{ value: "all" | ArtifactStatus; labelKey: string }> = [
  { value: "all", labelKey: "contentLibrary.filterAll" },
  { value: "pending", labelKey: "contentLibrary.filterPending" },
  { value: "approved", labelKey: "contentLibrary.filterApproved" },
  { value: "rejected", labelKey: "contentLibrary.filterRejected" },
];

export function ContentLibraryPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  // Default to "pending" so users immediately see content that needs review
  const [filter, setFilter] = useState<"all" | ArtifactStatus>("pending");
  const [autoRefresh, setAutoRefresh] = useState(true);


  const artifactsQuery = useQuery({
    queryKey: ["artifacts"],
    queryFn: () => api.getArtifacts({ limit: 200 }),
    refetchInterval: autoRefresh ? 8000 : false,
  });

  const artifacts: Artifact[] = (artifactsQuery.data?.items ?? []).filter(
    (a) => filter === "all" || a.status === filter,
  );

  useToastError(artifactsQuery.error, t("contentLibrary.errorLoad"));

  const updateMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: ArtifactStatus }) =>
      api.updateArtifactStatus(id, status),
    onSuccess: (_data, vars) => {
      queryClient.invalidateQueries({ queryKey: ["artifacts"] });
      toast({
        title: t("contentLibrary.updated"),
        description: t("contentLibrary.updatedDesc", { status: STATUS_LABELS[vars.status] }),
      });
    },
    onError: () => toast({ title: t("contentLibrary.errorUpdate"), variant: "destructive" }),
  });

  return (
    <div className="space-y-6">
      <PageHeader title={t("contentLibrary.title")} description={t("contentLibrary.description")} />

      {/* ── Filter row ────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap gap-3">
        <div className="flex gap-1 rounded-lg border bg-muted/30 p-1">
          {FILTER_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setFilter(opt.value)}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm font-medium transition-all",
                filter === opt.value
                  ? "bg-background shadow text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t(opt.labelKey as any)}
              {opt.value !== "all" && (
                <span className="ml-1.5 text-xs opacity-60">
                  {(artifactsQuery.data?.items ?? []).filter((a) => a.status === opt.value).length}
                </span>
              )}
            </button>
          ))}
        </div>
        <label className="inline-flex h-9 items-center gap-2 rounded-md border bg-card px-3 text-sm ml-auto">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
          />
          {t("contentLibrary.autoRefresh")}
        </label>
      </div>

      {/* ── Content grid ──────────────────────────────────────────────────── */}
      {artifactsQuery.isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-64 rounded-xl" />
          ))}
        </div>
      ) : artifacts.length === 0 ? (
        <div className="rounded-lg border border-dashed p-10 text-center">
          {filter === "pending" ? (
            <>
              <div className="text-4xl mb-3">✅</div>
              <p className="font-medium">All caught up!</p>
              <p className="mt-1 text-sm text-muted-foreground">
                No videos waiting for review. Run an automation to generate new content.
              </p>
            </>
          ) : filter === "approved" ? (
            <>
              <div className="text-4xl mb-3">🎬</div>
              <p className="font-medium">No approved content yet</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Approve content from the <strong>Awaiting Review</strong> tab to make it available for publishing.
              </p>
            </>
          ) : (
            <>
              <div className="text-4xl mb-3">📂</div>
              <p className="font-medium">{t("contentLibrary.noContent")}</p>
              <p className="mt-1 text-sm text-muted-foreground">
                {t("contentLibrary.noContentDesc")}
              </p>
            </>
          )}
        </div>

      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {artifacts.map((art) => (
            <ContentCard
              key={art.id}
              artifact={art}
              onApprove={() => updateMutation.mutate({ id: art.id, status: "approved" })}
              onReject={() => updateMutation.mutate({ id: art.id, status: "rejected" })}
              isPending={updateMutation.isPending}
              t={t}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Content Card ──────────────────────────────────────────────────────────────
function ContentCard({
  artifact,
  onApprove,
  onReject,
  isPending,
  t,
}: {
  artifact: Artifact;
  onApprove: () => void;
  onReject: () => void;
  isPending: boolean;
  t: (key: string) => string;
}) {
  const status = artifact.status ?? "pending";

  return (
    <Card className="overflow-hidden border transition-all hover:shadow-md">
      {/* Preview */}
      <div className="relative bg-muted/50">
        {artifact.artifact_type === "video" ? (
          <video
            src={artifact.storage_uri}
            className="h-44 w-full object-cover bg-black"
            controls
            muted
            preload="metadata"
          />
        ) : artifact.artifact_type === "image" ? (
          <img
            src={artifact.storage_uri}
            alt="content preview"
            className="h-44 w-full object-cover"
          />
        ) : (
          <div className="flex h-44 items-center justify-center text-muted-foreground">
            <div className="text-center">
              <div className="text-4xl mb-2">📄</div>
              <div className="text-xs uppercase font-mono">{artifact.artifact_type}</div>
            </div>
          </div>
        )}

        {/* Status badge overlay */}
        <div className="absolute right-2 top-2">
          <span
            className={cn(
              "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold",
              STATUS_BADGE[status as ArtifactStatus] ?? "bg-muted text-muted-foreground",
            )}
          >
            {STATUS_LABELS[status as ArtifactStatus] ?? status}
          </span>
        </div>
      </div>

      {/* Actions */}
      <CardContent className="p-3">
        <div className="mb-2 text-xs text-muted-foreground font-mono truncate" title={artifact.storage_uri}>
          {artifact.storage_uri.split("/").pop() ?? artifact.storage_uri}
        </div>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            className="flex-1 border-emerald-300 text-emerald-700 hover:bg-emerald-50 dark:border-emerald-700 dark:text-emerald-400"
            disabled={status === "approved" || isPending}
            onClick={onApprove}
          >
            <CheckCircle2 className="h-3.5 w-3.5" />
            {t("contentLibrary.approve")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="flex-1 border-red-300 text-red-700 hover:bg-red-50 dark:border-red-700 dark:text-red-400"
            disabled={status === "rejected" || isPending}
            onClick={onReject}
          >
            <XCircle className="h-3.5 w-3.5" />
            {t("contentLibrary.reject")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
