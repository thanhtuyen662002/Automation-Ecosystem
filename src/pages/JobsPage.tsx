import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState<"newest" | "oldest" | "name">("newest");
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: api.getJobs });
  useToastError(jobs.error, t("jobs.errorLoad"));
  const filteredJobs = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    return [...(Array.isArray(jobs.data) ? jobs.data : [])]
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
        title={t("jobs.title")}
        description={t("jobs.description")}
        action={
          <Link
            to="/create"
            className="inline-flex h-9 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
          >
            {t("jobs.createJob")}
          </Link>
        }
      />
      <div className="flex flex-wrap gap-3">
        <Input className="w-72" placeholder={t("jobs.searchPlaceholder")} value={search} onChange={(event) => setSearch(event.target.value)} />
        <Select value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">{t("jobs.allStatuses")}</option>
          <option value="pending">{t("jobs.pending")}</option>
          <option value="running">{t("jobs.running")}</option>
          <option value="completed">{t("jobs.completed")}</option>
          <option value="failed">{t("jobs.failed")}</option>
        </Select>
        <Select value={sort} onChange={(event) => setSort(event.target.value as "newest" | "oldest" | "name")}>
          <option value="newest">{t("jobs.newestFirst")}</option>
          <option value="oldest">{t("jobs.oldestFirst")}</option>
          <option value="name">{t("jobs.sortByName")}</option>
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
                  <TableHead>{t("jobs.colJobId")}</TableHead>
                  <TableHead>{t("jobs.colWorkflow")}</TableHead>
                  <TableHead>{t("jobs.colStatus")}</TableHead>
                  <TableHead>{t("jobs.colCreatedAt")}</TableHead>
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
            <EmptyState title={t("jobs.noJobsFound")} description={t("jobs.noJobsFoundDesc")} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
