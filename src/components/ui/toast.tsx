import * as React from "react";
import { createRoot } from "react-dom/client";
import { cn } from "@/lib/utils";

type Toast = {
  id: number;
  title: string;
  description?: string;
  variant?: "default" | "destructive";
};

let pushToast: ((toast: Toast) => void) | null = null;
let id = 0;

export function toast(input: Omit<Toast, "id">) {
  pushToast?.({ ...input, id: ++id });
}

export function Toaster() {
  const [items, setItems] = React.useState<Toast[]>([]);
  React.useEffect(() => {
    pushToast = (item) => {
      setItems((current) => [...current, item]);
      window.setTimeout(() => {
        setItems((current) => current.filter((toastItem) => toastItem.id !== item.id));
      }, 4200);
    };
    return () => {
      pushToast = null;
    };
  }, []);

  return (
    <div className="fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2">
      {items.map((item) => (
        <div
          key={item.id}
          className={cn(
            "rounded-lg border bg-card p-4 text-sm shadow-soft",
            item.variant === "destructive" && "border-red-200 bg-red-50 text-red-900",
          )}
        >
          <div className="font-medium">{item.title}</div>
          {item.description ? <div className="mt-1 text-muted-foreground">{item.description}</div> : null}
        </div>
      ))}
    </div>
  );
}

export function renderToastPortal(container: HTMLElement) {
  createRoot(container).render(<Toaster />);
}
