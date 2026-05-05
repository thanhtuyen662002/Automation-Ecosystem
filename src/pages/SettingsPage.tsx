import { useMemo, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "@/services/api";
import type { PolicyPreset, PolicyRuleDraft } from "@/types/api";
import { JsonViewer } from "@/components/JsonViewer";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { toast } from "@/components/ui/toast";

const presets: Record<PolicyPreset, Pick<PolicyRuleDraft, "posts_per_day" | "delay_minutes">> = {
  Safe: { posts_per_day: 3, delay_minutes: 90 },
  Medium: { posts_per_day: 8, delay_minutes: 35 },
  Aggressive: { posts_per_day: 18, delay_minutes: 10 },
};

const presetKeys: PolicyPreset[] = ["Safe", "Medium", "Aggressive"];

export function SettingsPage() {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<PolicyRuleDraft>({
    preset: "Safe",
    action_type: "post",
    posts_per_day: presets.Safe.posts_per_day,
    delay_minutes: presets.Safe.delay_minutes,
  });
  const payload = useMemo(() => api.buildPolicyRulePayload(draft), [draft]);

  const presetLabels: Record<PolicyPreset, string> = {
    Safe: t("settings.presetSafe"),
    Medium: t("settings.presetMedium"),
    Aggressive: t("settings.presetAggressive"),
  };

  function applyPreset(preset: PolicyPreset) {
    setDraft((current) => ({ ...current, preset, ...presets[preset] }));
  }

  function savePolicy() {
    window.localStorage.setItem("automation-policy-rule-draft", JSON.stringify(payload));
    toast({ title: t("settings.policySaved"), description: t("settings.policySavedDesc") });
  }

  return (
    <div className="space-y-6">
      <PageHeader title={t("settings.title")} description={t("settings.description")} />
      <div className="grid gap-4 xl:grid-cols-[1fr_0.9fr]">
        <Card className="shadow-soft">
          <CardHeader>
            <div className="flex items-center gap-3">
              <div className="grid h-10 w-10 place-items-center rounded-lg bg-emerald-50 text-emerald-700">
                <ShieldCheck className="h-5 w-5" />
              </div>
              <div>
                <CardTitle>{t("settings.postingPolicy")}</CardTitle>
                <CardDescription>{t("settings.postingPolicyDesc")}</CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="grid gap-3 sm:grid-cols-3">
              {presetKeys.map((preset) => (
                <button
                  key={preset}
                  onClick={() => applyPreset(preset)}
                  className={`rounded-lg border p-4 text-left transition hover:-translate-y-0.5 hover:shadow-soft ${
                    draft.preset === preset ? "border-primary bg-blue-50 text-blue-950 dark:bg-blue-950/30 dark:text-blue-100" : "bg-card"
                  }`}
                >
                  <div className="font-medium">{presetLabels[preset]}</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {t("settings.perDay", { n: presets[preset].posts_per_day })} • {t("settings.minDelay", { n: presets[preset].delay_minutes })}
                  </div>
                </button>
              ))}
            </div>
            <label className="block space-y-2">
              <span className="text-sm font-medium">{t("settings.labelActionType")}</span>
              <Select value={draft.action_type} onChange={(event) => setDraft((current) => ({ ...current, action_type: event.target.value }))}>
                <option value="post">{t("settings.actionPost")}</option>
                <option value="comment">{t("settings.actionComment")}</option>
                <option value="follow">{t("settings.actionFollow")}</option>
                <option value="message">{t("settings.actionMessage")}</option>
              </Select>
            </label>
            <Slider label={t("settings.labelPostsPerDay")} min={1} max={30} value={draft.posts_per_day} onChange={(value) => setDraft((current) => ({ ...current, posts_per_day: value }))} />
            <Slider label={t("settings.labelDelay")} suffix={t("settings.suffixMin")} min={5} max={180} value={draft.delay_minutes} onChange={(value) => setDraft((current) => ({ ...current, delay_minutes: value }))} />
            <Button onClick={savePolicy}>{t("settings.savePolicy")}</Button>
          </CardContent>
        </Card>
        <JsonViewer title="policy_rules payload" value={payload} />
      </div>
    </div>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  suffix,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  suffix?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block space-y-2">
      <div className="flex justify-between text-sm">
        <span className="font-medium">{label}</span>
        <span className="text-muted-foreground">{value}{suffix ? ` ${suffix}` : ""}</span>
      </div>
      <input className="w-full accent-blue-600" type="range" min={min} max={max} value={value} onChange={(event) => onChange(Number(event.target.value))} />
    </label>
  );
}
