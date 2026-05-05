import { useMemo, useState } from "react";
import { ShieldCheck } from "lucide-react";
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

export function SettingsPage() {
  const [draft, setDraft] = useState<PolicyRuleDraft>({
    preset: "Safe",
    action_type: "post",
    posts_per_day: presets.Safe.posts_per_day,
    delay_minutes: presets.Safe.delay_minutes,
  });
  const payload = useMemo(() => api.buildPolicyRulePayload(draft), [draft]);

  function applyPreset(preset: PolicyPreset) {
    setDraft((current) => ({ ...current, preset, ...presets[preset] }));
  }

  function savePolicy() {
    window.localStorage.setItem("automation-policy-rule-draft", JSON.stringify(payload));
    toast({ title: "Policy draft saved", description: "The policy_rules payload is ready for backend persistence." });
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Settings" description="Policy Rules for safer, more predictable account behavior." />
      <div className="grid gap-4 xl:grid-cols-[1fr_0.9fr]">
        <Card className="shadow-soft">
          <CardHeader>
            <div className="flex items-center gap-3">
              <div className="grid h-10 w-10 place-items-center rounded-lg bg-emerald-50 text-emerald-700">
                <ShieldCheck className="h-5 w-5" />
              </div>
              <div>
                <CardTitle>Posting policy</CardTitle>
                <CardDescription>Choose a preset, then fine-tune limits.</CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="grid gap-3 sm:grid-cols-3">
              {(Object.keys(presets) as PolicyPreset[]).map((preset) => (
                <button
                  key={preset}
                  onClick={() => applyPreset(preset)}
                  className={`rounded-lg border p-4 text-left transition hover:-translate-y-0.5 hover:shadow-soft ${
                    draft.preset === preset ? "border-primary bg-blue-50 text-blue-950 dark:bg-blue-950/30 dark:text-blue-100" : "bg-card"
                  }`}
                >
                  <div className="font-medium">{preset}</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {presets[preset].posts_per_day}/day • {presets[preset].delay_minutes}m delay
                  </div>
                </button>
              ))}
            </div>
            <label className="block space-y-2">
              <span className="text-sm font-medium">Action type</span>
              <Select value={draft.action_type} onChange={(event) => setDraft((current) => ({ ...current, action_type: event.target.value }))}>
                <option value="post">Post</option>
                <option value="comment">Comment</option>
                <option value="follow">Follow</option>
                <option value="message">Message</option>
              </Select>
            </label>
            <Slider label="Posts per day" min={1} max={30} value={draft.posts_per_day} onChange={(value) => setDraft((current) => ({ ...current, posts_per_day: value }))} />
            <Slider label="Delay between actions" suffix="min" min={5} max={180} value={draft.delay_minutes} onChange={(value) => setDraft((current) => ({ ...current, delay_minutes: value }))} />
            <Button onClick={savePolicy}>Save policy draft</Button>
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
