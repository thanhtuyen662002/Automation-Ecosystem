import { useEffect } from "react";
import { toast } from "@/components/ui/toast";

export function useToastError(error: unknown, title = "Something went wrong") {
  useEffect(() => {
    if (!error) return;
    const message = error instanceof Error ? error.message : "Please try again.";
    toast({ title, description: message, variant: "destructive" });
  }, [error, title]);
}
