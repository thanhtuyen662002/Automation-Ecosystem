import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";
import { api } from "@/services/api";
import { JsonViewer } from "@/components/JsonViewer";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToastError } from "@/hooks/useToastError";
import { compactId } from "@/lib/utils";

export function TaskDetailPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const [tab, setTab] = useState<"payload" | "result" | "error" | "executions">("payload");
  const task = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => api.getTask(taskId ?? ""),
    enabled: Boolean(taskId),
    refetchInterval: 5000,
  });
  useToastError(task.error, "Could not load task");

  if (task.isLoading) {
    return <Skeleton className="h-96" />;
  }
  if (!task.data) {
    return <PageHeader title="Task not found" description="The selected task could not be loaded." />;
  }

  return (
    <div className="space-y-6">
      <PageHeader title={`Task ${compactId(task.data.id)}`} description="Payload, result, retry state, and execution notes." />
      <div className="grid gap-4 md:grid-cols-4">
        <InfoCard label="Status" value={<StatusBadge status={task.data.status} />} />
        <InfoCard label="Task type" value={task.data.task_type} />
        <InfoCard label="Retry count" value={`${task.data.retry_count} / ${task.data.max_retries}`} />
        <InfoCard label="Job" value={compactId(task.data.job_id)} />
      </div>
      <Tabs>
        <TabsList>
          <TabsTrigger active={tab === "payload"} onClick={() => setTab("payload")}>Payload</TabsTrigger>
          <TabsTrigger active={tab === "result"} onClick={() => setTab("result")}>Result</TabsTrigger>
          <TabsTrigger active={tab === "error"} onClick={() => setTab("error")}>Error</TabsTrigger>
          <TabsTrigger active={tab === "executions"} onClick={() => setTab("executions")}>Executions</TabsTrigger>
        </TabsList>
        {tab === "payload" ? <JsonViewer title="Payload" value={task.data.payload} /> : null}
        {tab === "result" ? <JsonViewer title="Result" value={task.data.result ?? {}} /> : null}
        {tab === "error" ? <JsonViewer title="Error" value={{ error_type: task.data.error_type, error_message: task.data.error_message }} /> : null}
        {tab === "executions" ? (
          <Card>
            <CardHeader>
              <CardTitle>Execution history</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Execution history is stored in Postgres under task_executions. The current API does not expose a read endpoint for it yet.
              </p>
            </CardContent>
          </Card>
        ) : null}
      </Tabs>
    </div>
  );
}

function InfoCard({ label, value }: { label: string; value: ReactNode }) {
  return (
    <Card>
      <CardContent className="pt-5">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="mt-2 text-sm font-medium">{value}</div>
      </CardContent>
    </Card>
  );
}
