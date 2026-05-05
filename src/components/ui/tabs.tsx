import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Tabs({ children }: { children: ReactNode }) {
  return <div className="space-y-3">{children}</div>;
}

export function TabsList({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("inline-flex rounded-md bg-muted p-1", className)}>{children}</div>;
}

export function TabsTrigger({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      className={cn(
        "rounded-sm px-3 py-1.5 text-sm font-medium transition-colors",
        active ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
      )}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
