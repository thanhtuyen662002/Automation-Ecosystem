import { useEffect, useMemo, useState } from "react";
import { Eye, EyeOff, KeyRound, Save, ShieldCheck } from "lucide-react";
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

const LS_API_KEYS = "automation-api-keys";

type ApiKeys = {
  gemini: string;
  huggingface: string;
  openai: string;
};

function loadApiKeys(): ApiKeys {
  try {
    const raw = localStorage.getItem(LS_API_KEYS);
    if (raw) return { gemini: "", huggingface: "", openai: "", ...JSON.parse(raw) };
  } catch {
    // ignore
  }
  return { gemini: "", huggingface: "", openai: "" };
}

export function SettingsPage() {
  const { t } = useTranslation();

  // ── API Keys state ─────────────────────────────────────────────────────────
  const [apiKeys, setApiKeys] = useState<ApiKeys>(loadApiKeys);
  const [showKeys, setShowKeys] = useState<Record<keyof ApiKeys, boolean>>({
    gemini: false,
    huggingface: false,
    openai: false,
  });

  useEffect(() => {
    const saved = loadApiKeys();
    setApiKeys(saved);
  }, []);

  function saveApiKeys() {
    localStorage.setItem(LS_API_KEYS, JSON.stringify(apiKeys));
    toast({ title: t("settings.apiKeysSaved"), description: t("settings.apiKeysSavedDesc") });
  }

  function toggleShow(key: keyof ApiKeys) {
    setShowKeys((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  // ── Policy state ───────────────────────────────────────────────────────────
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

      {/* ── API Keys ──────────────────────────────────────────────────────────── */}
      <Card className="shadow-soft">
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-violet-50 text-violet-700 dark:bg-violet-950/40 dark:text-violet-300">
              <KeyRound className="h-5 w-5" />
            </div>
            <div>
              <CardTitle>{t("settings.apiKeysTitle")}</CardTitle>
              <CardDescription>{t("settings.apiKeysDesc")}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {([
            { key: "gemini" as const,      label: t("settings.keyGemini"),      placeholder: "AIza..." },
            { key: "huggingface" as const, label: t("settings.keyHuggingFace"), placeholder: "hf_..." },
            { key: "openai" as const,      label: t("settings.keyOpenAI"),      placeholder: "sk-..." },
          ] satisfies { key: keyof ApiKeys; label: string; placeholder: string }[]).map(({ key, label, placeholder }) => (
            <label key={key} className="block space-y-1.5">
              <span className="text-sm font-medium">{label}</span>
              <div className="relative flex items-center">
                <input
                  id={`api-key-${key}`}
                  type={showKeys[key] ? "text" : "password"}
                  value={apiKeys[key]}
                  onChange={(e) => setApiKeys((prev) => ({ ...prev, [key]: e.target.value }))}
                  placeholder={placeholder}
                  autoComplete="off"
                  spellCheck={false}
                  className="w-full rounded-lg border border-input bg-background px-3 py-2 pr-10 font-mono text-sm shadow-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/40 transition"
                />
                <button
                  type="button"
                  onClick={() => toggleShow(key)}
                  className="absolute right-2.5 text-muted-foreground hover:text-foreground transition"
                  aria-label={showKeys[key] ? t("settings.hideKey") : t("settings.showKey")}
                >
                  {showKeys[key] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </label>
          ))}
          <div className="pt-1">
            <Button id="save-api-keys-btn" onClick={saveApiKeys} className="flex items-center gap-2">
              <Save className="h-4 w-4" />
              {t("settings.saveApiKeys")}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            {t("settings.apiKeysNote")}
          </p>
        </CardContent>
      </Card>

      {/* ── Posting policy ────────────────────────────────────────────────────── */}
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
