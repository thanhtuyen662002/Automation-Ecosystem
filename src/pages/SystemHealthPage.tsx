import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Activity, Database, Server, Workflow } from "lucide-react";
import { api } from "@/services/api";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { useToastError } from "@/hooks/useToastError";

export function SystemHealthPage() {
  const health = useQuery({ queryKey: ["deep-health"], queryFn: api.getDeepHealth, refetchInterval: 5000 });
  useToastError(health.error, "Could not load system health");

  return (
    <div className="space-y-6">
      <PageHeader title="System health" description="A quick read on database connectivity, workers, and scheduler state." />
      {health.isLoading ? (
        <Skeleton className="h-48" />
      ) : (
        <div className="grid gap-4 md:grid-cols-3">
          <HealthCard icon={<Database className="h-5 w-5" />} title="Database" ok={Boolean(health.data?.database.ok)} detail={health.data?.database.error ?? "Connection check passed"} heartbeat="Checked every 5 seconds" />
          <HealthCard icon={<Server className="h-5 w-5" />} title="Worker" ok={Boolean(health.data?.worker.running)} detail={health.data?.worker.running ? "Worker runtime is active" : "No worker reported by this API process"} heartbeat="Worker heartbeat is stored per execution" />
          <HealthCard icon={<Workflow className="h-5 w-5" />} title="Scheduler" ok={Boolean(health.data?.scheduler.running)} detail={health.data?.scheduler.running ? "Auto dispatch loop is active" : "Scheduler is not running"} heartbeat="Scheduler loop is supervised by the API process" />
        </div>
      )}
    </div>
  );
}

function HealthCard({ icon, title, ok, detail, heartbeat }: { icon: ReactNode; title: string; ok: boolean; detail: string; heartbeat: string }) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div className="flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-md bg-muted">{icon}</div>
          <CardTitle>{title}</CardTitle>
        </div>
        <Badge variant={ok ? "success" : "retry"}>{ok ? "OK" : "Check"}</Badge>
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
