import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation();
  const { taskId } = useParams<{ taskId: string }>();
  const [tab, setTab] = useState<"payload" | "result" | "error" | "executions">("payload");
  const task = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => api.getTask(taskId ?? ""),
    enabled: Boolean(taskId),
    refetchInterval: 5000,
  });
  useToastError(task.error, t("taskDetail.errorLoad"));

  if (task.isLoading) {
    return <Skeleton className="h-96" />;
  }
  if (!task.data) {
    return <PageHeader title={t("taskDetail.notFound")} description={t("taskDetail.notFoundDesc")} />;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("taskDetail.title", { id: compactId(task.data.id) })}
        description={t("taskDetail.description")}
      />
      <div className="grid gap-4 md:grid-cols-4">
        <InfoCard label={t("taskDetail.labelStatus")} value={<StatusBadge status={task.data.status} />} />
        <InfoCard label={t("taskDetail.labelTaskType")} value={task.data.task_type} />
        <InfoCard label={t("taskDetail.labelRetryCount")} value={`${task.data.retry_count} / ${task.data.max_retries}`} />
        <InfoCard label={t("taskDetail.labelJob")} value={compactId(task.data.job_id)} />
      </div>
      <Tabs>
        <TabsList>
          <TabsTrigger active={tab === "payload"} onClick={() => setTab("payload")}>{t("taskDetail.tabPayload")}</TabsTrigger>
          <TabsTrigger active={tab === "result"} onClick={() => setTab("result")}>{t("taskDetail.tabResult")}</TabsTrigger>
          <TabsTrigger active={tab === "error"} onClick={() => setTab("error")}>{t("taskDetail.tabError")}</TabsTrigger>
          <TabsTrigger active={tab === "executions"} onClick={() => setTab("executions")}>{t("taskDetail.tabExecutions")}</TabsTrigger>
        </TabsList>
        {tab === "payload" ? <JsonViewer title={t("taskDetail.tabPayload")} value={task.data.payload} /> : null}
        {tab === "result" ? <JsonViewer title={t("taskDetail.tabResult")} value={task.data.result ?? {}} /> : null}
        {tab === "error" ? <JsonViewer title={t("taskDetail.tabError")} value={{ error_type: task.data.error_type, error_message: task.data.error_message }} /> : null}
        {tab === "executions" ? (
          <Card>
            <CardHeader>
              <CardTitle>{t("taskDetail.executionHistory")}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                {t("taskDetail.executionHistoryNote")}
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
