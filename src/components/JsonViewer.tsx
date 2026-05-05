import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";

export function JsonViewer({ title, value }: { title: string; value: unknown }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="rounded-lg border bg-card">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div className="text-sm font-medium">{title}</div>
        <Button variant="ghost" size="sm" onClick={() => setOpen((current) => !current)}>
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </Button>
      </div>
      {open ? (
        <pre className="max-h-80 overflow-auto p-4 text-xs leading-6 text-slate-700">
          {JSON.stringify(value ?? {}, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}
