import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useMemo, useState } from "react";
import { api } from "@/services/api";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { compactId, formatDate } from "@/lib/utils";
import { useToastError } from "@/hooks/useToastError";

export function JobsPage() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState<"newest" | "oldest" | "name">("newest");
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: api.getJobs });
  useToastError(jobs.error, "Could not load jobs");
  const filteredJobs = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    return [...(jobs.data ?? [])]
      .filter((job) => !normalized || job.workflow_name.toLowerCase().includes(normalized) || job.id.includes(normalized))
      .filter((job) => !status || job.status === status)
      .sort((a, b) => {
        if (sort === "name") return a.workflow_name.localeCompare(b.workflow_name);
        const left = new Date(a.created_at).getTime();
        const right = new Date(b.created_at).getTime();
        return sort === "newest" ? right - left : left - right;
      });
  }, [jobs.data, search, sort, status]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Jobs"
        description="Workflow runs created by the orchestration API."
        action={
          <Link
            to="/create"
            className="inline-flex h-9 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
          >
            Create job
          </Link>
        }
      />
      <div className="flex flex-wrap gap-3">
        <Input className="w-72" placeholder="Search jobs" value={search} onChange={(event) => setSearch(event.target.value)} />
        <Select value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">All statuses</option>
          <option value="pending">Pending</option>
          <option value="running">Running</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
        </Select>
        <Select value={sort} onChange={(event) => setSort(event.target.value as "newest" | "oldest" | "name")}>
          <option value="newest">Newest first</option>
          <option value="oldest">Oldest first</option>
          <option value="name">Name</option>
        </Select>
      </div>
      <Card>
        <CardContent className="p-0">
          {jobs.isLoading ? (
            <div className="space-y-3 p-5">
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
            </div>
          ) : filteredJobs.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>job_id</TableHead>
                  <TableHead>workflow</TableHead>
                  <TableHead>status</TableHead>
                  <TableHead>created_at</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredJobs.map((job) => (
                  <TableRow key={job.id}>
                    <TableCell className="font-mono text-xs">
                      <Link to={`/jobs/${job.id}`} className="hover:underline">{compactId(job.id)}</Link>
                    </TableCell>
                    <TableCell className="font-medium">
                      <Link to={`/jobs/${job.id}`} className="hover:underline">{job.workflow_name}</Link>
                    </TableCell>
                    <TableCell><StatusBadge status={job.status.toUpperCase()} /></TableCell>
                    <TableCell>{formatDate(job.created_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <EmptyState title="No jobs found" description="Try a different search, filter, or create a new workflow." />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
