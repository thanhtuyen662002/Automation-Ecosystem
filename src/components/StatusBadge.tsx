import { Badge } from "@/components/ui/badge";
import type { TaskStatus } from "@/types/api";

const variantByStatus: Record<TaskStatus, "success" | "failed" | "running" | "pending" | "retry" | "default"> = {
  SUCCESS: "success",
  FAILED: "failed",
  RUNNING: "running",
  PENDING: "pending",
  READY: "pending",
  RETRY: "retry",
  CANCELED: "default",
};

export function StatusBadge({ status }: { status: TaskStatus | string }) {
  const variant = variantByStatus[status as TaskStatus] ?? "default";
  return <Badge variant={variant}>{status}</Badge>;
}
