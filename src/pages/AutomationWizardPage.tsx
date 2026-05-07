import { useState } from "react";
import { useNavigate, useSearchParams, Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Check, ChevronLeft, ChevronRight, Link2, Rocket, Wifi } from "lucide-react";
import { api } from "@/services/api";
import type { Account, Artifact } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { cn } from "@/lib/utils";
import { friendlyError } from "@/lib/friendlyError";

// ── Types ─────────────────────────────────────────────────────────────────────
type Platform = "tiktok" | "youtube" | "facebook" | "crosspost";

interface WizardState {
  platform: Platform | null;
  accountId: string;
  contentId: string;
  contentUri: string;
  caption: string;
  title: string;       // youtube only
  description: string; // youtube + facebook
}

const TOTAL_STEPS = 5;

const PLATFORM_CONFIG: Record<
  Exclude<Platform, "crosspost">,
  { label: string; icon: string; taskType: string; color: string }
> = {
  tiktok: { label: "TikTok", icon: "🎵", taskType: "publish_tiktok", color: "border-[#EE1D52] bg-[#EE1D52]/5" },
  youtube: { label: "YouTube", icon: "▶️", taskType: "publish_youtube", color: "border-red-500 bg-red-500/5" },
  facebook: { label: "Facebook", icon: "📘", taskType: "publish_facebook", color: "border-blue-600 bg-blue-600/5" },
};

// ── Build job payload from wizard state ───────────────────────────────────────
function buildJobPayload(state: WizardState): { workflow_name: string; tasks: object[] } {
  const base = {
    account_id: state.accountId,
    video_path: state.contentUri,
  };

  if (state.platform === "crosspost") {
    return {
      workflow_name: "Cross-post: YouTube & Facebook",
      tasks: [
        {
          task_key: "publish_youtube",
          task_type: "publish_youtube",
          payload: { ...base, title: state.title, description: state.description },
          depends_on: [],
        },
        {
          task_key: "publish_facebook",
          task_type: "publish_facebook",
          payload: { ...base, description: state.description },
          depends_on: [],
        },
      ],
    };
  }

  const platform = state.platform as Exclude<Platform, "crosspost">;
  const extraPayload =
    platform === "tiktok"
      ? { caption: state.caption }
      : platform === "youtube"
      ? { title: state.title, description: state.description }
      : { description: state.description };

  return {
    workflow_name: `Publish to ${PLATFORM_CONFIG[platform].label}`,
    tasks: [
      {
        task_key: "publish",
        task_type: PLATFORM_CONFIG[platform].taskType,
        payload: { ...base, ...extraPayload },
        depends_on: [],
      },
    ],
  };
}

// ── Main Wizard ───────────────────────────────────────────────────────────────
export function AutomationWizardPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  // Pre-select platform from template query param
  const templateParam = searchParams.get("template");
  const initialPlatform: Platform | null =
    templateParam === "tiktok" ? "tiktok"
    : templateParam === "crosspost" ? "crosspost"
    : null;

  const [step, setStep] = useState(1);
  const [state, setState] = useState<WizardState>({
    platform: initialPlatform,
    accountId: "",
    contentId: "",
    contentUri: "",
    caption: "",
    title: "",
    description: "",
  });

  const update = <K extends keyof WizardState>(key: K, val: WizardState[K]) =>
    setState((s) => ({ ...s, [key]: val }));

  // ── Data queries ───────────────────────────────────────────────────────────
  const accountsQuery = useQuery({
    queryKey: ["accounts"],
    queryFn: () => api.getAccounts({ limit: 200 }),
    enabled: step >= 2,
  });

  const artifactsQuery = useQuery({
    queryKey: ["artifacts"],
    queryFn: () => api.getArtifacts({ limit: 200 }),
    enabled: step >= 3,
  });

  // Filter accounts for selected platform(s)
  const platformsToFilter: string[] =
    state.platform === "crosspost"
      ? ["youtube", "facebook"]
      : state.platform
      ? [state.platform]
      : [];

  const filteredAccounts: Account[] = (accountsQuery.data?.items ?? []).filter(
    (a) => a.status === "healthy" && a.session_valid && platformsToFilter.includes(a.platform),
  );

  // All accounts for platform (even not-yet-connected) — for better empty state messaging
  const allAccountsForPlatform: Account[] = (accountsQuery.data?.items ?? []).filter(
    (a) => platformsToFilter.includes(a.platform),
  );

  const approvedArtifacts: Artifact[] = (artifactsQuery.data?.items ?? []).filter(
    (a) => a.status === "approved",
  );

  // ── Submit mutation ────────────────────────────────────────────────────────
  const launchMutation = useMutation({
    mutationFn: () => {
      const payload = buildJobPayload(state);
      return fetch("/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).then(async (res) => {
        if (!res.ok) throw new Error((await res.json()).detail ?? "Failed");
        return res.json();
      });
    },
    onSuccess: () => {
      toast({ title: t("wizard.successTitle"), description: t("wizard.successDesc") });
      navigate("/jobs");
    },
    onError: (err: Error) => {
      // Map raw error codes to human messages
      const friendly = friendlyError(null, err.message);
      toast({
        title: friendly.title,
        description: friendly.description,
        variant: "destructive",
      });
    },
  });

  // ── Step navigation ────────────────────────────────────────────────────────
  const canNext: boolean = (() => {
    if (step === 1) return state.platform !== null;
    if (step === 2) return state.accountId !== "";
    if (step === 3) return state.contentId !== "";
    if (step === 4) {
      if (state.platform === "tiktok") return state.caption.trim().length > 0;
      if (state.platform === "youtube") return state.title.trim().length > 0;
      return state.description.trim().length > 0; // facebook / crosspost
    }
    return true;
  })();

  const stepTitles = [
    t("wizard.step1Title"),
    t("wizard.step2Title"),
    t("wizard.step3Title"),
    t("wizard.step4Title"),
    t("wizard.step5Title"),
  ];

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      {/* ── Header ────────────────────────────────────────────────────────── */}
      <div>
        <h1 className="text-2xl font-semibold">{t("wizard.title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("wizard.stepIndicator", { current: step, total: TOTAL_STEPS })} — {stepTitles[step - 1]}
        </p>
      </div>

      {/* ── Step indicator ────────────────────────────────────────────────── */}
      <div className="flex items-center gap-2">
        {Array.from({ length: TOTAL_STEPS }, (_, i) => i + 1).map((s) => (
          <div key={s} className="flex items-center gap-2">
            <div
              className={cn(
                "flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold transition-all",
                s < step
                  ? "bg-emerald-500 text-white"
                  : s === step
                  ? "bg-primary text-primary-foreground shadow-md ring-2 ring-primary/30"
                  : "bg-muted text-muted-foreground",
              )}
            >
              {s < step ? <Check className="h-3.5 w-3.5" /> : s}
            </div>
            {s < TOTAL_STEPS && (
              <div className={cn("h-0.5 w-8 rounded-full", s < step ? "bg-emerald-400" : "bg-muted")} />
            )}
          </div>
        ))}
      </div>

      {/* ── Step content ──────────────────────────────────────────────────── */}
      <Card>
        <CardContent className="p-6">
          {step === 1 && <Step1Platform state={state} update={update} t={t} />}
          {step === 2 && (
            <Step2Account
              state={state}
              update={update}
              t={t}
              accounts={filteredAccounts}
              allAccountsForPlatform={allAccountsForPlatform}
              isLoading={accountsQuery.isLoading}
            />
          )}
          {step === 3 && (
            <Step3Content
              state={state}
              update={update}
              t={t}
              artifacts={approvedArtifacts}
              isLoading={artifactsQuery.isLoading}
            />
          )}
          {step === 4 && <Step4Caption state={state} update={update} t={t} />}
          {step === 5 && <Step5Review state={state} t={t} accounts={accountsQuery.data?.items ?? []} />}
        </CardContent>
      </Card>

      {/* ── Navigation buttons ────────────────────────────────────────────── */}
      <div className="flex justify-between">
        {step > 1 ? (
          <Button variant="outline" onClick={() => setStep((s) => s - 1)}>
            <ChevronLeft className="h-4 w-4" />
            {t("wizard.back")}
          </Button>
        ) : (
          <Link to="/automations" className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm font-medium hover:bg-muted transition-colors">
            <ChevronLeft className="h-4 w-4" />
            {t("wizard.back")}
          </Link>
        )}

        {step < TOTAL_STEPS ? (
          <Button disabled={!canNext} onClick={() => setStep((s) => s + 1)}>
            {t("wizard.next")}
            <ChevronRight className="h-4 w-4" />
          </Button>
        ) : (
          <Button
            disabled={launchMutation.isPending}
            onClick={() => launchMutation.mutate()}
            className="bg-emerald-600 hover:bg-emerald-700 text-white"
          >
            <Rocket className="h-4 w-4" />
            {launchMutation.isPending ? t("wizard.launching") : t("wizard.launch")}
          </Button>
        )}
      </div>
    </div>
  );
}

// ── Step 1: Platform ──────────────────────────────────────────────────────────
function Step1Platform({
  state,
  update,
  t,
}: {
  state: WizardState;
  update: <K extends keyof WizardState>(k: K, v: WizardState[K]) => void;
  t: TFunction;
}) {
  const platforms: { id: Platform; icon: string; label: string; sub: string }[] = [
    { id: "tiktok", icon: "🎵", label: "TikTok", sub: "Short-form video" },
    { id: "youtube", icon: "▶️", label: "YouTube", sub: "Long-form & Shorts" },
    { id: "facebook", icon: "📘", label: "Facebook", sub: "Social video" },
    { id: "crosspost", icon: "📡", label: "YouTube + Facebook", sub: "Cross-post simultaneously" },
  ];

  return (
    <div className="space-y-4">
      <div>
        <h2 className="font-semibold text-lg">{t("wizard.step1Title")}</h2>
        <p className="text-sm text-muted-foreground mt-1">{t("wizard.step1Desc")}</p>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {platforms.map((p) => (
          <button
            key={p.id}
            onClick={() => { update("platform", p.id); update("accountId", ""); }}
            className={cn(
              "flex items-center gap-3 rounded-xl border-2 p-4 text-left transition-all",
              state.platform === p.id
                ? "border-primary bg-primary/5 shadow-sm"
                : "border-border hover:border-muted-foreground/40 hover:bg-muted/30",
            )}
          >
            <span className="text-3xl">{p.icon}</span>
            <div>
              <div className="font-semibold">{p.label}</div>
              <div className="text-xs text-muted-foreground">{p.sub}</div>
            </div>
            {state.platform === p.id && (
              <Check className="ml-auto h-5 w-5 text-primary" />
            )}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Step 2: Account ───────────────────────────────────────────────────────────
function Step2Account({
  state,
  update,
  t,
  accounts,
  allAccountsForPlatform,
  isLoading,
}: {
  state: WizardState;
  update: <K extends keyof WizardState>(k: K, v: WizardState[K]) => void;
  t: TFunction;
  accounts: Account[];
  allAccountsForPlatform: Account[];
  isLoading: boolean;
}) {
  // Smart empty state: are there accounts but none connected?
  const hasAccountsButNoneConnected =
    allAccountsForPlatform.length > 0 && accounts.length === 0;

  return (
    <div className="space-y-4">
      <div>
        <h2 className="font-semibold text-lg">{t("wizard.step2Title")}</h2>
        <p className="text-sm text-muted-foreground mt-1">{t("wizard.step2Desc")}</p>
      </div>
      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-14" />
          <Skeleton className="h-14" />
        </div>
      ) : accounts.length === 0 ? (
        <div className="rounded-lg border border-dashed p-6 text-center space-y-3">
          {hasAccountsButNoneConnected ? (
            // Accounts exist but session not connected
            <>
              <div className="flex justify-center">
                <div className="flex h-12 w-12 items-center justify-center rounded-full bg-amber-100 dark:bg-amber-900/30">
                  <Wifi className="h-6 w-6 text-amber-600" />
                </div>
              </div>
              <p className="font-medium text-sm">Account not connected</p>
              <p className="text-sm text-muted-foreground">
                You have accounts but none are connected yet.
                Click <strong>Connect</strong> on the Accounts page to log in.
              </p>
              <Link
                to="/accounts"
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                <Link2 className="h-3.5 w-3.5" />
                Connect account
              </Link>
            </>
          ) : (
            // No accounts at all
            <>
              <p className="font-medium text-sm">{t("wizard.noAccountsForPlatform")}</p>
              <Link to="/accounts" className="mt-2 inline-block text-primary hover:underline underline-offset-4">
                {t("wizard.noAccountsLink")}
              </Link>
            </>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          {accounts.map((acc) => (
            <button
              key={acc.id}
              onClick={() => update("accountId", acc.id)}
              className={cn(
                "flex w-full items-center gap-3 rounded-xl border-2 p-4 text-left transition-all",
                state.accountId === acc.id
                  ? "border-primary bg-primary/5 shadow-sm"
                  : "border-border hover:border-muted-foreground/40 hover:bg-muted/30",
              )}
            >
              <span className="text-2xl">
                {acc.platform === "tiktok" ? "🎵" : acc.platform === "youtube" ? "▶️" : "📘"}
              </span>
              <div className="flex-1">
                <div className="font-semibold font-mono">{acc.account_handle}</div>
                <div className="text-xs capitalize text-muted-foreground flex items-center gap-1.5">
                  {acc.platform}
                  <span className="inline-flex items-center gap-0.5 text-emerald-600 dark:text-emerald-400">
                    <Wifi className="h-3 w-3" />
                    Connected
                  </span>
                </div>
              </div>
              {state.accountId === acc.id && <Check className="h-5 w-5 text-primary" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}


// ── Step 3: Content ───────────────────────────────────────────────────────────
function Step3Content({
  state,
  update,
  t,
  artifacts,
  isLoading,
}: {
  state: WizardState;
  update: <K extends keyof WizardState>(k: K, v: WizardState[K]) => void;
  t: TFunction;
  artifacts: Artifact[];
  isLoading: boolean;
}) {
  return (
    <div className="space-y-4">
      <div>
        <h2 className="font-semibold text-lg">{t("wizard.step3Title")}</h2>
        <p className="text-sm text-muted-foreground mt-1">{t("wizard.step3Desc")}</p>
      </div>
      {isLoading ? (
        <div className="grid grid-cols-2 gap-3">
          <Skeleton className="h-32 rounded-xl" />
          <Skeleton className="h-32 rounded-xl" />
        </div>
      ) : artifacts.length === 0 ? (
        <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
          <p className="font-medium">{t("wizard.noContent")}</p>
          <Link to="/artifacts" className="mt-2 inline-block text-primary hover:underline underline-offset-4">
            {t("wizard.noContentLink")}
          </Link>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {artifacts.map((art) => (
            <button
              key={art.id}
              onClick={() => { update("contentId", art.id); update("contentUri", art.storage_uri); }}
              className={cn(
                "relative overflow-hidden rounded-xl border-2 transition-all",
                state.contentId === art.id
                  ? "border-primary shadow-md ring-2 ring-primary/20"
                  : "border-border hover:border-muted-foreground/40",
              )}
            >
              {art.artifact_type === "video" ? (
                <video
                  src={art.storage_uri}
                  className="h-28 w-full object-cover bg-black"
                  muted
                  preload="metadata"
                />
              ) : art.artifact_type === "image" ? (
                <img
                  src={art.storage_uri}
                  alt="content"
                  className="h-28 w-full object-cover"
                />
              ) : (
                <div className="h-28 flex items-center justify-center bg-muted text-muted-foreground text-xs">
                  {art.artifact_type}
                </div>
              )}
              {state.contentId === art.id && (
                <div className="absolute inset-0 flex items-center justify-center bg-primary/20">
                  <div className="rounded-full bg-primary p-1">
                    <Check className="h-4 w-4 text-primary-foreground" />
                  </div>
                </div>
              )}
              <div className="px-2 py-1.5 text-center text-xs font-medium capitalize">
                {art.artifact_type}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Step 4: Caption ───────────────────────────────────────────────────────────
function Step4Caption({
  state,
  update,
  t,
}: {
  state: WizardState;
  update: <K extends keyof WizardState>(k: K, v: WizardState[K]) => void;
  t: TFunction;
}) {
  const isTikTok = state.platform === "tiktok";
  const isYouTube = state.platform === "youtube";
  const isFacebook = state.platform === "facebook";
  const isCrosspost = state.platform === "crosspost";

  return (
    <div className="space-y-5">
      <div>
        <h2 className="font-semibold text-lg">{t("wizard.step4Title")}</h2>
        <p className="text-sm text-muted-foreground mt-1">{t("wizard.step4Desc")}</p>
      </div>

      {/* Caption — TikTok */}
      {isTikTok && (
        <FieldArea
          label={t("wizard.labelCaption")}
          hint={t("wizard.captionHint")}
          value={state.caption}
          onChange={(v) => update("caption", v)}
          placeholder={t("wizard.captionPlaceholder")}
          maxLength={2200}
          rows={4}
        />
      )}

      {/* Title + Description — YouTube */}
      {(isYouTube || isCrosspost) && (
        <>
          <FieldInput
            label={t("wizard.labelTitle")}
            value={state.title}
            onChange={(v) => update("title", v)}
            placeholder={t("wizard.titlePlaceholder")}
          />
          <FieldArea
            label={t("wizard.labelDescription")}
            value={state.description}
            onChange={(v) => update("description", v)}
            placeholder={t("wizard.descriptionPlaceholder")}
            rows={4}
          />
        </>
      )}

      {/* Description — Facebook */}
      {isFacebook && (
        <FieldArea
          label={t("wizard.labelDescription")}
          hint={t("wizard.captionHint")}
          value={state.description}
          onChange={(v) => update("description", v)}
          placeholder={t("wizard.descriptionPlaceholder")}
          rows={4}
        />
      )}
    </div>
  );
}

function FieldInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-sm font-medium">{label}</label>
      <input
        className="w-full rounded-lg border bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
    </div>
  );
}

function FieldArea({
  label,
  hint,
  value,
  onChange,
  placeholder,
  maxLength,
  rows = 4,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  maxLength?: number;
  rows?: number;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between">
        <label className="text-sm font-medium">{label}</label>
        {maxLength && (
          <span className={cn("text-xs", value.length > maxLength * 0.8 ? "text-amber-500" : "text-muted-foreground")}>
            {value.length}/{maxLength}
          </span>
        )}
      </div>
      <textarea
        rows={rows}
        className="w-full rounded-lg border bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 resize-none"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        maxLength={maxLength}
      />
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

// ── Step 5: Review ────────────────────────────────────────────────────────────
function Step5Review({
  state,
  t,
  accounts,
}: {
  state: WizardState;
  t: (key: string) => string;
  accounts: Account[];
}) {
  const account = accounts.find((a) => a.id === state.accountId);

  const rows: { label: string; value: string }[] = [
    {
      label: t("wizard.reviewPlatform"),
      value:
        state.platform === "crosspost"
          ? "YouTube + Facebook"
          : state.platform?.charAt(0).toUpperCase() + (state.platform?.slice(1) ?? ""),
    },
    {
      label: t("wizard.reviewAccount"),
      value: account ? `${account.account_handle} (${account.platform})` : "—",
    },
    {
      label: t("wizard.reviewContent"),
      value: state.contentUri ? state.contentUri.split("/").pop() ?? state.contentUri : "—",
    },
    {
      label: t("wizard.reviewCaption"),
      value: state.caption || state.title || state.description || "—",
    },
  ];

  return (
    <div className="space-y-4">
      <div>
        <h2 className="font-semibold text-lg">{t("wizard.step5Title")}</h2>
        <p className="text-sm text-muted-foreground mt-1">{t("wizard.step5Desc")}</p>
      </div>
      <div className="divide-y rounded-xl border">
        {rows.map((row) => (
          <div key={row.label} className="flex gap-4 px-4 py-3">
            <span className="w-36 shrink-0 text-sm font-medium text-muted-foreground">{row.label}</span>
            <span className="text-sm font-medium break-all">{row.value}</span>
          </div>
        ))}
      </div>
      <div className="flex items-center gap-2 rounded-lg bg-emerald-50 border border-emerald-200 px-4 py-3 text-sm text-emerald-800 dark:bg-emerald-950/30 dark:border-emerald-800 dark:text-emerald-300">
        <Rocket className="h-4 w-4 shrink-0" />
        Ready to launch. Workers will pick this up within seconds.
      </div>
    </div>
  );
}
