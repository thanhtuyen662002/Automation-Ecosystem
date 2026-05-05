import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
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

export function CreateJobPage() {
  const { t } = useTranslation();
  const [workflowName, setWorkflowName] = useState("");
  const [taskType, setTaskType] = useState<TaskKind>("ai");
  const [primaryValue, setPrimaryValue] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [advancedJson, setAdvancedJson] = useState("{\n  \"max_chars\": 280\n}");
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const fieldLabels: Record<TaskKind, { primary: string; placeholder: string }> = {
    ai: { primary: t("createJob.fieldAiPrimary"), placeholder: t("createJob.fieldAiPlaceholder") },
    browser: { primary: t("createJob.fieldBrowserPrimary"), placeholder: t("createJob.fieldBrowserPlaceholder") },
    media: { primary: t("createJob.fieldMediaPrimary"), placeholder: t("createJob.fieldMediaPlaceholder") },
  };

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
      toast({ title: t("createJob.jobCreated"), description: t("createJob.jobCreatedDesc") });
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      navigate("/jobs");
    },
  });
  useToastError(createJob.error, t("createJob.errorCreate"));

  return (
    <div className="space-y-6">
      <PageHeader title={t("createJob.title")} description={t("createJob.description")} />
      <Card className="max-w-3xl shadow-soft">
        <CardHeader>
          <CardTitle>{t("createJob.builderTitle")}</CardTitle>
          <CardDescription>{t("createJob.builderDesc")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <label className="block space-y-2">
            <span className="text-sm font-medium">{t("createJob.labelWorkflowName")}</span>
            <Input value={workflowName} onChange={(event) => setWorkflowName(event.target.value)} placeholder={t("createJob.workflowNamePlaceholder")} />
          </label>
          <label className="block space-y-2">
            <span className="text-sm font-medium">{t("createJob.labelTaskType")}</span>
            <Select value={taskType} onChange={(event) => setTaskType(event.target.value as TaskKind)}>
              <option value="ai">{t("createJob.taskTypeAi")}</option>
              <option value="browser">{t("createJob.taskTypeBrowser")}</option>
              <option value="media">{t("createJob.taskTypeMedia")}</option>
            </Select>
          </label>
          <label className="block space-y-2">
            <span className="text-sm font-medium">{fieldLabels[taskType].primary}</span>
            <Input value={primaryValue} onChange={(event) => setPrimaryValue(event.target.value)} placeholder={fieldLabels[taskType].placeholder} />
          </label>
          <Tabs>
            <TabsList>
              <TabsTrigger active={!advanced} onClick={() => setAdvanced(false)}>{t("createJob.modeSimple")}</TabsTrigger>
              <TabsTrigger active={advanced} onClick={() => setAdvanced(true)}>{t("createJob.modeAdvanced")}</TabsTrigger>
            </TabsList>
            {advanced ? (
              <textarea
                className="min-h-40 w-full rounded-md border bg-background p-3 font-mono text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                value={advancedJson}
                onChange={(event) => setAdvancedJson(event.target.value)}
              />
            ) : (
              <div className="rounded-lg border bg-muted/50 p-4 text-sm text-muted-foreground">
                {t("createJob.payloadPreview")} <span className="font-medium text-foreground">{Object.keys(payload).join(", ")}</span>
              </div>
            )}
          </Tabs>
          <Button disabled={!workflowName || !primaryValue || (advanced && !isValidJsonObject(advancedJson)) || createJob.isPending} onClick={() => createJob.mutate()}>
            {t("createJob.createJobBtn")}
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
