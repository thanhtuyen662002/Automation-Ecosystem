import { useParams, Link } from "react-router-dom";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
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
  const { jobId } = useParams<{ jobId: string }>();
  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.getJob(jobId ?? ""),
    enabled: Boolean(jobId),
  });
  useToastError(job.error, "Could not load job");

  if (job.isLoading) return <Skeleton className="h-96" />;
  if (!job.data) return <PageHeader title="Job not found" description="The selected workflow could not be loaded." />;

  return (
    <div className="space-y-6">
      <PageHeader title={job.data.workflow_name} description={`Job ${compactId(job.data.id)} created ${formatDate(job.data.created_at)}`} />
      <div className="grid gap-4 md:grid-cols-3">
        <InfoCard label="Status" value={<StatusBadge status={job.data.status.toUpperCase()} />} />
        <InfoCard label="Priority" value={job.data.priority} />
        <InfoCard label="Tasks" value={job.data.tasks.length} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <JsonViewer title="Workflow input preview" value={job.data.input} />
        <JsonViewer title="Workflow metadata" value={job.data.metadata} />
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Tasks</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {job.data.tasks.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>task_id</TableHead>
                  <TableHead>task_type</TableHead>
                  <TableHead>status</TableHead>
                  <TableHead>retry_count</TableHead>
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
            <EmptyState title="No tasks in this job" description="This workflow does not have task records yet." />
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
