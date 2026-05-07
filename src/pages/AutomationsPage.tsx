import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Bot, Check, LayoutGrid, Link2, MonitorPlay, Rocket, X, Zap } from "lucide-react";
import { api } from "@/services/api";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/StatusBadge";
import { formatDate, cn } from "@/lib/utils";
import { friendlyError } from "@/lib/friendlyError";

// ── Template definitions ──────────────────────────────────────────────────────
const TEMPLATES = [
  {
    id: "tiktok",
    icon: "🎵",
    gradient: "from-[#010101] via-[#69C9D0] to-[#EE1D52]",
    textClass: "text-white",
    badgeClass: "bg-white/20 text-white",
  },
  {
    id: "crosspost",
    icon: "📡",
    gradient: "from-[#FF0000] via-[#FF0000]/60 to-[#1877F2]",
    textClass: "text-white",
    badgeClass: "bg-white/20 text-white",
  },
  {
    id: "caption",
    icon: "✨",
    gradient: "from-violet-600 via-purple-500 to-fuchsia-500",
    textClass: "text-white",
    badgeClass: "bg-white/20 text-white",
  },
] as const;

type TemplateId = (typeof TEMPLATES)[number]["id"];

const ONBOARDING_KEY = "onboarding_dismissed_v1";

export function AutomationsPage() {
  const { t } = useTranslation();

  // ── Onboarding checklist state ────────────────────────────────────────────
  const [onboardingDismissed, setOnboardingDismissed] = useState(
    () => localStorage.getItem(ONBOARDING_KEY) === "true",
  );

  const dismissOnboarding = () => {
    localStorage.setItem(ONBOARDING_KEY, "true");
    setOnboardingDismissed(true);
  };

  // Pull accounts to check if any are connected (for onboarding checklist)
  const accountsQuery = useQuery({
    queryKey: ["accounts"],
    queryFn: () => api.getAccounts({ limit: 200 }),
    enabled: !onboardingDismissed,
  });

  const policyRulesQuery = useQuery({
    queryKey: ["policy-rules"],
    queryFn: () => api.getPolicyRules({ limit: 10 }),
    enabled: !onboardingDismissed,
  });

  const hasConnectedAccount = (accountsQuery.data?.items ?? []).some(
    (a) => a.session_valid,
  );
  const hasPolicyRule = (policyRulesQuery.data?.items ?? []).length > 0;

  // Pull recent jobs to show "recent automations" section
  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.getJobs(),
    refetchInterval: 15000,
  });
  const rawJobs = jobsQuery.data;
  const recentJobs = (Array.isArray(rawJobs) ? rawJobs : []).slice(0, 5);
  const hasCompletedJob = recentJobs.some((j) => j.status === "completed");

  // Onboarding checklist steps
  const checklistItems = [
    {
      id: "connect",
      label: "Connect your first account",
      done: hasConnectedAccount,
      route: "/accounts",
      cta: "Go to Accounts",
    },
    {
      id: "policy",
      label: "Posting limits set",
      done: hasPolicyRule,
      route: "/posting-limits",
      cta: "Set limits",
    },
    {
      id: "run",
      label: "Run your first automation",
      done: hasCompletedJob,
      route: "/automations/create",
      cta: "Create automation",
    },
  ];
  const allDone = checklistItems.every((i) => i.done);

  return (
    <div className="space-y-10">
      <PageHeader
        title={t("automations.title")}
        description={t("automations.description")}
      />

      {/* ── Getting Started checklist ──────────────────────────────────────── */}
      {!onboardingDismissed && (
        <Card className="relative border-primary/20 bg-primary/5 dark:bg-primary/10">
          <button
            onClick={dismissOnboarding}
            className="absolute right-3 top-3 rounded-md p-1 text-muted-foreground hover:text-foreground transition-colors"
            aria-label="Dismiss"
          >
            <X className="h-4 w-4" />
          </button>
          <CardContent className="p-5">
            <div className="mb-4">
              <h2 className="font-semibold">
                {allDone ? "✅ You're all set!" : "🚀 Getting started"}
              </h2>
              <p className="text-sm text-muted-foreground mt-0.5">
                {allDone
                  ? "Everything is configured. You're ready to automate."
                  : "Complete these steps to run your first automation."}
              </p>
            </div>
            <div className="space-y-2">
              {checklistItems.map((item) => (
                <div
                  key={item.id}
                  className={cn(
                    "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors",
                    item.done ? "bg-emerald-50 dark:bg-emerald-950/30" : "bg-background border",
                  )}
                >
                  <div
                    className={cn(
                      "flex h-5 w-5 shrink-0 items-center justify-center rounded-full",
                      item.done
                        ? "bg-emerald-500 text-white"
                        : "border-2 border-muted-foreground/30",
                    )}
                  >
                    {item.done && <Check className="h-3 w-3" />}
                  </div>
                  <span
                    className={cn(
                      "flex-1",
                      item.done && "text-emerald-700 dark:text-emerald-400 line-through",
                    )}
                  >
                    {item.label}
                  </span>
                  {!item.done && (
                    <Link
                      to={item.route}
                      className="text-xs font-medium text-primary hover:underline underline-offset-4"
                    >
                      {item.cta} →
                    </Link>
                  )}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* ── Template cards ────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
        {TEMPLATES.map((tmpl) => (
          <TemplateCard key={tmpl.id} templateId={tmpl.id} tmpl={tmpl} t={t} />
        ))}
      </div>

      {/* ── Advanced builder link ─────────────────────────────────────────── */}
      <div className="flex items-center gap-3 rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
        <Bot className="h-5 w-5 shrink-0" />
        <span>{t("automations.advancedLink")}</span>
        <Link
          to="/advanced/workflow-builder"
          className="ml-auto inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline font-medium"
        >
          {t("automations.advancedLinkDesc")}
          <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>

      {/* ── Recent automations ────────────────────────────────────────────── */}
      <div>
        <h2 className="mb-4 text-base font-semibold">{t("automations.myAutomations")}</h2>
        {recentJobs.length === 0 ? (
          <div className="rounded-lg border border-dashed p-8 text-center">
            <Rocket className="mx-auto mb-3 h-9 w-9 text-muted-foreground/50" />
            <p className="font-medium">{t("automations.noAutomations")}</p>
            <p className="mt-1 text-sm text-muted-foreground">{t("automations.noAutomationsDesc")}</p>
          </div>
        ) : (
          <div className="space-y-2">
            {recentJobs.map((job) => {
              const errInfo =
                job.status === "failed" && (job.error_type || job.error_message)
                  ? friendlyError(job.error_type, job.error_message)
                  : null;
              return (
                <Link
                  key={job.id}
                  to={`/jobs/${job.id}`}
                  className="flex items-center gap-3 rounded-lg border bg-card px-4 py-3 text-sm transition hover:bg-muted/40"
                >
                  <LayoutGrid className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <div className="flex-1 min-w-0">
                    <div className="font-medium truncate">{job.workflow_name}</div>
                    {errInfo && (
                      <div className="text-xs text-destructive truncate mt-0.5">
                        ⚠ {errInfo.title} — {errInfo.description}
                      </div>
                    )}
                  </div>
                  <StatusBadge status={job.status} />
                  <span className="text-xs text-muted-foreground shrink-0">{formatDate(job.created_at)}</span>
                  <ArrowRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                </Link>
              );
            })}
            <div className="pt-2 text-right">
              <Link to="/jobs" className="text-xs text-primary hover:underline underline-offset-4">
                View all jobs →
              </Link>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Template Card ─────────────────────────────────────────────────────────────
function TemplateCard({
  templateId,
  tmpl,
  t,
}: {
  templateId: TemplateId;
  tmpl: (typeof TEMPLATES)[number];
  t: ReturnType<typeof useTranslation>["t"];
}) {
  const titleKey = `automations.template${templateId.charAt(0).toUpperCase() + templateId.slice(1)}` as const;
  const descKey = `${titleKey}Desc` as const;

  const ICONS: Record<TemplateId, typeof MonitorPlay> = {
    tiktok: MonitorPlay,
    crosspost: Zap,
    caption: Bot,
  };
  const Icon = ICONS[templateId];

  return (
    <Card className="group overflow-hidden border-0 shadow-md transition-all duration-200 hover:-translate-y-1 hover:shadow-xl">
      {/* gradient header */}
      <div className={`bg-gradient-to-br ${tmpl.gradient} p-6`}>
        <div className="flex items-start justify-between">
          <span className="text-4xl">{tmpl.icon}</span>
          <span
            className={`rounded-full px-2 py-0.5 text-xs font-semibold ${tmpl.badgeClass}`}
          >
            Template
          </span>
        </div>
        <h3 className={`mt-4 text-lg font-bold ${tmpl.textClass}`}>
          {t(titleKey as any)}
        </h3>
        <p className={`mt-1 text-sm leading-relaxed opacity-85 ${tmpl.textClass}`}>
          {t(descKey as any)}
        </p>
      </div>
      {/* CTA */}
      <CardContent className="p-4">
        <Link to={`/automations/create?template=${templateId}`}>
          <Button className="w-full gap-2 group-hover:gap-3 transition-all">
            <Icon className="h-4 w-4" />
            {t("automations.getStarted")}
            <ArrowRight className="h-3.5 w-3.5" />
          </Button>
        </Link>
      </CardContent>
    </Card>
  );
}
