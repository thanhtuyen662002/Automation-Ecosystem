import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Activity, Database, Server, Workflow } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "@/services/api";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { useToastError } from "@/hooks/useToastError";

export function SystemHealthPage() {
  const { t } = useTranslation();
  const health = useQuery({ queryKey: ["deep-health"], queryFn: api.getDeepHealth, refetchInterval: 5000 });
  useToastError(health.error, t("system.errorLoad"));

  return (
    <div className="space-y-6">
      <PageHeader title={t("system.title")} description={t("system.description")} />
      {health.isLoading ? (
        <Skeleton className="h-48" />
      ) : health.isError || !health.data ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-6 text-center text-sm text-destructive">
          {t("system.errorLoad")}
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-3">
          <HealthCard
            icon={<Database className="h-5 w-5" />}
            title={t("system.database")}
            ok={Boolean(health.data.database?.ok)}
            detail={health.data.database?.error ?? t("system.dbConnectionOk")}
            heartbeat={t("system.heartbeatDb")}
          />
          <HealthCard
            icon={<Server className="h-5 w-5" />}
            title={t("system.worker")}
            ok={Boolean(health.data.worker?.running)}
            detail={health.data.worker?.running ? t("system.workerActive") : t("system.workerInactive")}
            heartbeat={t("system.heartbeatWorker")}
          />
          <HealthCard
            icon={<Workflow className="h-5 w-5" />}
            title={t("system.scheduler")}
            ok={Boolean(health.data.scheduler?.running)}
            detail={health.data.scheduler?.running ? t("system.schedulerActive") : t("system.schedulerInactive")}
            heartbeat={t("system.heartbeatScheduler")}
          />
        </div>
      )}
    </div>
  );
}

function HealthCard({ icon, title, ok, detail, heartbeat }: { icon: ReactNode; title: string; ok: boolean; detail: string; heartbeat: string }) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div className="flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-md bg-muted">{icon}</div>
          <CardTitle>{title}</CardTitle>
        </div>
        <Badge variant={ok ? "success" : "retry"}>{ok ? t("system.statusOk") : t("system.statusCheck")}</Badge>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">{detail}</p>
        <div className="mt-4 flex items-center gap-2 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
          <Activity className="h-3.5 w-3.5" />
          {heartbeat}
        </div>
      </CardContent>
    </Card>
  );
}
