import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, XCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "@/services/api";
import type { Artifact } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toast } from "@/components/ui/toast";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { useToastError } from "@/hooks/useToastError";
import { cn } from "@/lib/utils";

const STATUS_LABEL_CLASS: Record<string, string> = {
  approved: "bg-emerald-50 text-emerald-700 border-emerald-200",
  rejected: "bg-red-50 text-red-700 border-red-200",
  pending: "bg-amber-50 text-amber-700 border-amber-200",
};

export function ArtifactsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [autoRefresh, setAutoRefresh] = useState(true);

  // ── Query ──────────────────────────────────────────────────────────────────
  const artifactsQuery = useQuery({
    queryKey: ["artifacts"],
    queryFn: () => api.getArtifacts({ limit: 200 }),
    refetchInterval: autoRefresh ? 8000 : false,
  });

  const artifacts: Artifact[] = artifactsQuery.data?.items ?? [];
  useToastError(artifactsQuery.error, t("artifacts.errorLoad"));

  // ── Mutation ───────────────────────────────────────────────────────────────
  const updateMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: "approved" | "rejected" }) =>
      api.updateArtifactStatus(id, status),
    onSuccess: (_data, vars) => {
      queryClient.invalidateQueries({ queryKey: ["artifacts"] });
      toast({
        title: t("artifacts.statusUpdated"),
        description: t("artifacts.statusUpdatedDesc", { status: vars.status }),
      });
    },
    onError: () => toast({ title: t("artifacts.errorUpdate"), variant: "destructive" }),
  });

  return (
    <div className="space-y-6">
      <PageHeader title={t("artifacts.title")} description={t("artifacts.description")} />

      <div className="flex gap-3">
        <label className="inline-flex h-9 items-center gap-2 rounded-md border bg-card px-3 text-sm">
          <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
          {t("artifacts.autoRefresh")}
        </label>
      </div>

      <Card>
        <CardContent className="p-0">
          {artifactsQuery.isLoading ? (
            <div className="space-y-3 p-5">
              <Skeleton className="h-24" />
              <Skeleton className="h-24" />
              <Skeleton className="h-24" />
            </div>
          ) : artifacts.length === 0 ? (
            <EmptyState title={t("artifacts.noArtifacts")} description={t("artifacts.noArtifactsDesc")} />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("artifacts.preview")}</TableHead>
                  <TableHead>{t("artifacts.details")}</TableHead>
                  <TableHead>{t("artifacts.status")}</TableHead>
                  <TableHead className="text-right">{t("artifacts.actions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {artifacts.map((art) => (
                  <TableRow
                    key={art.id}
                    className={cn(
                      art.status === "approved" && "bg-emerald-50/30 dark:bg-emerald-950/10",
                      art.status === "rejected" && "bg-red-50/30 dark:bg-red-950/10",
                      art.status === "pending" && "bg-amber-50/30 dark:bg-amber-950/10",
                    )}
                  >
                    {/* Preview */}
                    <TableCell className="w-56">
                      {art.artifact_type === "video" ? (
                        <video
                          controls
                          width={200}
                          src={art.storage_uri}
                          className="rounded border bg-black"
                        />
                      ) : art.artifact_type === "image" ? (
                        <img
                          src={art.storage_uri}
                          width={200}
                          className="rounded border object-cover"
                          alt="artifact"
                        />
                      ) : (
                        <span className="text-xs text-muted-foreground italic">No preview</span>
                      )}
                    </TableCell>

                    {/* Details */}
                    <TableCell className="max-w-xs">
                      <div className="text-xs space-y-1">
                        <div>
                          <span className="font-semibold text-muted-foreground">ID: </span>
                          <span className="font-mono">{art.id.slice(0, 8)}…</span>
                        </div>
                        <div>
                          <span className="font-semibold text-muted-foreground">Type: </span>
                          <span className="uppercase font-mono bg-muted px-1 rounded">{art.artifact_type}</span>
                        </div>
                        <div
                          className="break-all text-muted-foreground"
                          title={art.storage_uri}
                        >
                          {art.storage_uri.length > 60
                            ? art.storage_uri.slice(0, 60) + "…"
                            : art.storage_uri}
                        </div>
                      </div>
                    </TableCell>

                    {/* Status */}
                    <TableCell>
                      <span
                        className={cn(
                          "inline-flex px-2 py-1 rounded-full text-xs font-bold uppercase tracking-wider border",
                          STATUS_LABEL_CLASS[art.status] ?? "bg-muted text-muted-foreground",
                        )}
                      >
                        {art.status ?? "pending"}
                      </span>
                    </TableCell>

                    {/* Actions */}
                    <TableCell className="text-right">
                      <div className="inline-flex gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={art.status === "approved" || updateMutation.isPending}
                          onClick={() => updateMutation.mutate({ id: art.id, status: "approved" })}
                          className="border-emerald-200 text-emerald-700 hover:bg-emerald-50"
                        >
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          {t("artifacts.approve")}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={art.status === "rejected" || updateMutation.isPending}
                          onClick={() => updateMutation.mutate({ id: art.id, status: "rejected" })}
                          className="border-red-200 text-red-700 hover:bg-red-50"
                        >
                          <XCircle className="h-3.5 w-3.5" />
                          {t("artifacts.reject")}
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
