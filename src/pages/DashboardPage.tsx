import { useMemo } from "react";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Area, AreaChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ArrowUpRight, CheckCircle2, Clock3, XCircle } from "lucide-react";
import { api } from "@/services/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { StatusBadge } from "@/components/StatusBadge";
import { compactId } from "@/lib/utils";
import { useToastError } from "@/hooks/useToastError";

export function DashboardPage() {
  const stats = useQuery({ queryKey: ["stats"], queryFn: api.getStats, refetchInterval: 5000 });
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: api.getJobs });
  const tasks = useQuery({ queryKey: ["tasks", "dashboard"], queryFn: () => api.getTasks(), refetchInterval: 5000 });
  useToastError(stats.error || jobs.error || tasks.error);

  const successRate = stats.data?.total_tasks
    ? Math.round((stats.data.success / stats.data.total_tasks) * 100)
    : 0;
  const chartData = useMemo(
    () =>
      (tasks.data ?? []).slice(0, 12).map((task, index) => ({
        name: `T${index + 1}`,
        tasks: index + 1,
      })),
    [tasks.data],
  );
  const pieData = [
    { name: "Success", value: stats.data?.success ?? 0, color: "#10b981" },
    { name: "Failed", value: stats.data?.failed ?? 0, color: "#ef4444" },
  ];

  return (
    <div className="space-y-6">
      <PageHeader title="Dashboard" description="A calm overview of workflows, tasks, and system health." />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard title="Total jobs" value={jobs.data?.length ?? 0} icon={<ArrowUpRight className="h-4 w-4" />} loading={jobs.isLoading} />
        <MetricCard title="Running tasks" value={stats.data?.running ?? 0} icon={<Clock3 className="h-4 w-4" />} loading={stats.isLoading} />
        <MetricCard title="Failed tasks" value={stats.data?.failed ?? 0} icon={<XCircle className="h-4 w-4" />} loading={stats.isLoading} />
        <MetricCard title="Success rate" value={`${successRate}%`} icon={<CheckCircle2 className="h-4 w-4" />} loading={stats.isLoading} />
      </div>
      <div className="grid gap-4 xl:grid-cols-[1.4fr_0.8fr]">
        <Card>
          <CardHeader>
            <CardTitle>Tasks over time</CardTitle>
            <CardDescription>Recent task volume from the current API window.</CardDescription>
          </CardHeader>
          <CardContent>
            {tasks.isLoading ? (
              <Skeleton className="h-72" />
            ) : chartData.length ? (
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={chartData}>
                    <defs>
                      <linearGradient id="taskFill" x1="0" x2="0" y1="0" y2="1">
                        <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.25} />
                        <stop offset="95%" stopColor="#3b82f6" stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="name" tickLine={false} axisLine={false} />
                    <YAxis tickLine={false} axisLine={false} />
                    <Tooltip />
                    <Area type="monotone" dataKey="tasks" stroke="#2563eb" fill="url(#taskFill)" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <EmptyState title="No task activity yet" description="Create a job to see task movement here." />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Success vs Fail</CardTitle>
            <CardDescription>Outcome mix across known tasks.</CardDescription>
          </CardHeader>
          <CardContent>
            {stats.isLoading ? (
              <Skeleton className="h-72" />
            ) : (
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={pieData} innerRadius={60} outerRadius={92} dataKey="value" paddingAngle={4}>
                      {pieData.map((entry) => (
                        <Cell key={entry.name} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
      <div className="grid gap-4">
        <Card>
          <CardHeader>
            <CardTitle>Recent activity</CardTitle>
            <CardDescription>Latest tasks reported by the API.</CardDescription>
          </CardHeader>
          <CardContent>
            {tasks.isLoading ? (
              <div className="space-y-3">
                <Skeleton className="h-12" />
                <Skeleton className="h-12" />
                <Skeleton className="h-12" />
              </div>
            ) : tasks.data?.length ? (
              <div className="relative space-y-3 pl-4 before:absolute before:left-1 before:top-2 before:h-[calc(100%-1rem)] before:w-px before:bg-border">
                {tasks.data.slice(0, 6).map((task) => (
                  <div key={task.id} className="relative flex items-center justify-between rounded-lg border bg-card p-3 shadow-sm transition hover:-translate-y-0.5 hover:shadow-soft">
                    <div className="absolute -left-[18px] top-5 h-2.5 w-2.5 rounded-full bg-primary ring-4 ring-background" />
                    <div>
                      <div className="font-mono text-xs">{compactId(task.id)}</div>
                      <div className="mt-1 text-sm text-muted-foreground">{task.task_type}</div>
                    </div>
                    <StatusBadge status={task.status} />
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState title="No recent activity" description="Tasks will appear here as workers process them." />
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function MetricCard({
  title,
  value,
  icon,
  loading,
}: {
  title: string;
  value: string | number;
  icon: ReactNode;
  loading?: boolean;
}) {
  return (
    <Card>
      <CardContent className="pt-5">
        <div className="flex items-center justify-between">
          <div className="text-sm text-muted-foreground">{title}</div>
          <div className="grid h-8 w-8 place-items-center rounded-md bg-blue-50 text-blue-700">{icon}</div>
        </div>
        {loading ? <Skeleton className="mt-4 h-8 w-24" /> : <div className="mt-3 text-3xl font-semibold">{value}</div>}
      </CardContent>
    </Card>
  );
}
