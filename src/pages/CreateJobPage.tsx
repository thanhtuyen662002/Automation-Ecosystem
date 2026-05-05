import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "@/services/api";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "@/components/ui/toast";
import { useToastError } from "@/hooks/useToastError";

type TaskKind = "ai" | "browser" | "media";

const fieldLabels: Record<TaskKind, { primary: string; placeholder: string }> = {
  ai: { primary: "Prompt", placeholder: "Write a short launch caption" },
  browser: { primary: "URL", placeholder: "https://example.com" },
  media: { primary: "Input path", placeholder: "D:\\Media\\clip.mp4" },
};

export function CreateJobPage() {
  const [workflowName, setWorkflowName] = useState("");
  const [taskType, setTaskType] = useState<TaskKind>("ai");
  const [primaryValue, setPrimaryValue] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [advancedJson, setAdvancedJson] = useState("{\n  \"max_chars\": 280\n}");
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const payload = useMemo(() => {
    const base =
      taskType === "ai"
        ? { prompt: primaryValue }
        : taskType === "browser"
          ? { url: primaryValue }
          : { input_path: primaryValue };
    if (!advanced) return base;
    return { ...base, ...parseJsonObject(advancedJson) };
  }, [advanced, advancedJson, primaryValue, taskType]);

  const createJob = useMutation({
    mutationFn: () =>
      api.createJob({
        workflow_name: workflowName,
        tasks: [{ task_type: taskType, payload }],
      }),
    onSuccess: () => {
      toast({ title: "Job created", description: "Workers can now pick up this workflow." });
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      navigate("/jobs");
    },
  });
  useToastError(createJob.error, "Could not create job");

  return (
    <div className="space-y-6">
      <PageHeader title="Create job" description="Build a workflow without writing JSON. Advanced mode is available when needed." />
      <Card className="max-w-3xl shadow-soft">
        <CardHeader>
          <CardTitle>Workflow builder</CardTitle>
          <CardDescription>Choose a task type and fill in the fields. The system will create the task payload for you.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <label className="block space-y-2">
            <span className="text-sm font-medium">Workflow name</span>
            <Input value={workflowName} onChange={(event) => setWorkflowName(event.target.value)} placeholder="Daily content workflow" />
          </label>
          <label className="block space-y-2">
            <span className="text-sm font-medium">Task type</span>
            <Select value={taskType} onChange={(event) => setTaskType(event.target.value as TaskKind)}>
              <option value="ai">AI text</option>
              <option value="browser">Open webpage</option>
              <option value="media">Process media file</option>
            </Select>
          </label>
          <label className="block space-y-2">
            <span className="text-sm font-medium">{fieldLabels[taskType].primary}</span>
            <Input value={primaryValue} onChange={(event) => setPrimaryValue(event.target.value)} placeholder={fieldLabels[taskType].placeholder} />
          </label>
          <Tabs>
            <TabsList>
              <TabsTrigger active={!advanced} onClick={() => setAdvanced(false)}>Simple</TabsTrigger>
              <TabsTrigger active={advanced} onClick={() => setAdvanced(true)}>Advanced JSON</TabsTrigger>
            </TabsList>
            {advanced ? (
              <textarea
                className="min-h-40 w-full rounded-md border bg-background p-3 font-mono text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                value={advancedJson}
                onChange={(event) => setAdvancedJson(event.target.value)}
              />
            ) : (
              <div className="rounded-lg border bg-muted/50 p-4 text-sm text-muted-foreground">
                Payload preview: <span className="font-medium text-foreground">{Object.keys(payload).join(", ")}</span>
              </div>
            )}
          </Tabs>
          <Button disabled={!workflowName || !primaryValue || (advanced && !isValidJsonObject(advancedJson)) || createJob.isPending} onClick={() => createJob.mutate()}>
            Create job
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

function parseJsonObject(value: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(value || "{}") as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function isValidJsonObject(value: string): boolean {
  try {
    const parsed = JSON.parse(value || "{}") as unknown;
    return Boolean(parsed && typeof parsed === "object" && !Array.isArray(parsed));
  } catch {
    return false;
  }
}
