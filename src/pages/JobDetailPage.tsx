import { useParams, Link } from "react-router-dom";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/services/api";
import { EmptyState } from "@/components/EmptyState";
import { JsonViewer } from "@/components/JsonViewer";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToastError } from "@/hooks/useToastError";
import { compactId, formatDate } from "@/lib/utils";

export function JobDetailPage() {
  const { t } = useTranslation();
  const { jobId } = useParams<{ jobId: string }>();
  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.getJob(jobId ?? ""),
    enabled: Boolean(jobId),
  });
  useToastError(job.error, t("jobDetail.errorLoad"));

  if (job.isLoading) return <Skeleton className="h-96" />;
  if (!job.data)
    return <PageHeader title={t("jobDetail.notFound")} description={t("jobDetail.notFoundDesc")} />;

  return (
    <div className="space-y-6">
      <PageHeader
        title={job.data.workflow_name}
        description={t("jobDetail.jobCreated", { id: compactId(job.data.id), date: formatDate(job.data.created_at) })}
      />
      <div className="grid gap-4 md:grid-cols-3">
        <InfoCard label={t("jobDetail.labelStatus")} value={<StatusBadge status={job.data.status.toUpperCase()} />} />
        <InfoCard label={t("jobDetail.labelPriority")} value={job.data.priority} />
        <InfoCard label={t("jobDetail.labelTasks")} value={job.data.tasks.length} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <JsonViewer title={t("jobDetail.inputPreview")} value={job.data.input} />
        <JsonViewer title={t("jobDetail.metadata")} value={job.data.metadata} />
      </div>
      <Card>
        <CardHeader>
          <CardTitle>{t("jobDetail.tasksTitle")}</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {job.data.tasks.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("jobDetail.colTaskId")}</TableHead>
                  <TableHead>{t("jobDetail.colTaskType")}</TableHead>
                  <TableHead>{t("jobDetail.colStatus")}</TableHead>
                  <TableHead>{t("jobDetail.colRetryCount")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {job.data.tasks.map((task) => (
                  <TableRow key={task.id}>
                    <TableCell className="font-mono text-xs">
                      <Link to={`/tasks/${task.id}`} className="hover:underline">{compactId(task.id)}</Link>
                    </TableCell>
                    <TableCell className="font-medium">{task.task_type}</TableCell>
                    <TableCell><StatusBadge status={task.status} /></TableCell>
                    <TableCell>{task.retry_count} / {task.max_retries}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <EmptyState title={t("jobDetail.noTasksInJob")} description={t("jobDetail.noTasksInJobDesc")} />
          )}
        </CardContent>
      </Card>
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
