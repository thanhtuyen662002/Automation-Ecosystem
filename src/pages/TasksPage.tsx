import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "@/services/api";
import type { Task, TaskStatus } from "@/types/api";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toast } from "@/components/ui/toast";
import { compactId, formatDate } from "@/lib/utils";
import { useToastError } from "@/hooks/useToastError";

const statuses: Array<TaskStatus | ""> = ["", "PENDING", "READY", "RUNNING", "RETRY", "SUCCESS", "FAILED", "CANCELED"];

export function TasksPage() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<TaskStatus | "">("");
  const [taskType, setTaskType] = useState("");
  const [accountId, setAccountId] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [retryTask, setRetryTask] = useState<Task | null>(null);
  const queryClient = useQueryClient();
  const tasks = useQuery({
    queryKey: ["tasks", status, taskType, accountId],
    queryFn: () => api.getTasks({ status, task_type: taskType, account_id: accountId }),
    refetchInterval: autoRefresh ? 5000 : false,
  });
  const retry = useMutation({
    mutationFn: (taskId: string) => api.retryTask(taskId),
    onSuccess: () => {
      toast({ title: t("tasks.retryScheduled"), description: t("tasks.retryScheduledDesc") });
      setRetryTask(null);
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });
  useToastError(tasks.error || retry.error, t("tasks.errorLoad"));

  return (
    <div className="space-y-6">
      <PageHeader title={t("tasks.title")} description={t("tasks.description")} />
      <div className="flex flex-wrap gap-3">
        <Select value={status} onChange={(event) => setStatus(event.target.value as TaskStatus | "")}>
          {statuses.map((item) => (
            <option key={item || "all"} value={item}>
              {item || t("jobs.allStatuses")}
            </option>
          ))}
        </Select>
        <Input className="w-64" placeholder={t("tasks.filterByType")} value={taskType} onChange={(event) => setTaskType(event.target.value)} />
        <Input className="w-72" placeholder={t("tasks.filterByAccount")} value={accountId} onChange={(event) => setAccountId(event.target.value)} />
        <label className="inline-flex h-9 items-center gap-2 rounded-md border bg-card px-3 text-sm">
          <input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} />
          {t("tasks.autoRefresh")}
        </label>
      </div>
      <Card>
        <CardContent className="p-0">
          {tasks.isLoading ? (
            <div className="space-y-3 p-5">
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
            </div>
          ) : Array.isArray(tasks.data) && tasks.data.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("tasks.colTaskId")}</TableHead>
                  <TableHead>{t("tasks.colTaskType")}</TableHead>
                  <TableHead>{t("tasks.colStatus")}</TableHead>
                  <TableHead>{t("tasks.colRetryCount")}</TableHead>
                  <TableHead>{t("tasks.colCreatedAt")}</TableHead>
                  <TableHead className="text-right">{t("tasks.colAction")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(Array.isArray(tasks.data) ? tasks.data : []).map((task) => (
                  <TableRow key={task.id}>
                    <TableCell className="font-mono text-xs">
                      <Link to={`/tasks/${task.id}`} className="hover:underline">{compactId(task.id)}</Link>
                    </TableCell>
                    <TableCell className="font-medium">{task.task_type}</TableCell>
                    <TableCell><StatusBadge status={task.status} /></TableCell>
                    <TableCell>{task.retry_count} / {task.max_retries}</TableCell>
                    <TableCell>{formatDate(task.created_at ?? task.next_run_at)}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="outline" size="sm" disabled={task.status !== "FAILED"} onClick={() => setRetryTask(task)}>
                        <RotateCcw className="h-3.5 w-3.5" />
                        {t("tasks.retry")}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <EmptyState title={t("tasks.noTasksMatch")} description={t("tasks.noTasksMatchDesc")} />
          )}
        </CardContent>
      </Card>
      <ConfirmDialog
        open={retryTask !== null}
        title={t("tasks.retryDialogTitle")}
        description={t("tasks.retryDialogDesc")}
        confirmLabel={t("tasks.retryConfirm")}
        loading={retry.isPending}
        onClose={() => setRetryTask(null)}
        onConfirm={() => retryTask && retry.mutate(retryTask.id)}
      />
    </div>
  );
}
