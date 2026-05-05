import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { RotateCcw } from "lucide-react";
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
      toast({ title: "Task retry scheduled", description: "The task was moved back into the pending queue." });
      setRetryTask(null);
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });
  useToastError(tasks.error || retry.error, "Task action failed");

  return (
    <div className="space-y-6">
      <PageHeader title="Tasks" description="Atomic units of work handled by workers." />
      <div className="flex flex-wrap gap-3">
        <Select value={status} onChange={(event) => setStatus(event.target.value as TaskStatus | "")}>
          {statuses.map((item) => (
            <option key={item || "all"} value={item}>
              {item || "All statuses"}
            </option>
          ))}
        </Select>
        <Input className="w-64" placeholder="Filter by task_type" value={taskType} onChange={(event) => setTaskType(event.target.value)} />
        <Input className="w-72" placeholder="Filter by account_id" value={accountId} onChange={(event) => setAccountId(event.target.value)} />
        <label className="inline-flex h-9 items-center gap-2 rounded-md border bg-card px-3 text-sm">
          <input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} />
          Auto-refresh
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
          ) : tasks.data?.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>task_id</TableHead>
                  <TableHead>task_type</TableHead>
                  <TableHead>status</TableHead>
                  <TableHead>retry_count</TableHead>
                  <TableHead>created_at</TableHead>
                  <TableHead className="text-right">action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tasks.data.map((task) => (
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
                        Retry
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <EmptyState title="No tasks match" description="Try changing filters or creating a new job." />
          )}
        </CardContent>
      </Card>
      <ConfirmDialog
        open={retryTask !== null}
        title="Retry failed task?"
        description="This moves the task back to pending if retry budget is available."
        confirmLabel="Retry task"
        loading={retry.isPending}
        onClose={() => setRetryTask(null)}
        onConfirm={() => retryTask && retry.mutate(retryTask.id)}
      />
    </div>
  );
}
