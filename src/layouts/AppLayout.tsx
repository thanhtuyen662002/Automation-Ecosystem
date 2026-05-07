import { NavLink, Outlet } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  Brain,
  CheckCircle2,
  Film,
  LayoutDashboard,
  Server,
  Settings,
  Shield,
  UsersRound,
  Zap,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/services/api";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "@/components/ThemeToggle";

export function AppLayout() {
  const { t, i18n } = useTranslation();

  // ── Primary navigation ───────────────────────────────────────────────────
  const primaryNav = [
    { label: t("nav.dashboard"), href: "/", icon: LayoutDashboard },
    { label: t("nav.automations"), href: "/automations", icon: Zap },
    { label: t("nav.accounts"), href: "/accounts", icon: UsersRound },
    { label: t("nav.content"), href: "/content", icon: Film },
    { label: t("nav.postingLimits"), href: "/posting-limits", icon: Shield },
    { label: "Account Brain", href: "/account-brain", icon: Brain },
    { label: t("nav.system"), href: "/system", icon: Server },
    { label: t("nav.settings"), href: "/settings", icon: Settings },
  ];

  // ── Secondary (developer) navigation ─────────────────────────────────────
  const devNav = [
    { label: t("nav.advancedBuilder"), href: "/advanced/workflow-builder", icon: AlertTriangle },
  ];

  const health = useQuery({
    queryKey: ["deep-health"],
    queryFn: api.getDeepHealth,
    refetchInterval: 5000,
  });
  const ok = health.data?.status === "ok";

  function toggleLanguage() {
    void i18n.changeLanguage(i18n.language === "vi" ? "en" : "vi");
  }

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,rgba(59,130,246,0.10),transparent_32rem),linear-gradient(180deg,#f8fafc,#eef4ff)] transition-colors dark:bg-[radial-gradient(circle_at_top_left,rgba(59,130,246,0.16),transparent_32rem),linear-gradient(180deg,#0f172a,#111827)]">
      <aside className="fixed inset-y-0 left-0 hidden w-64 border-r bg-white/80 p-4 backdrop-blur-xl transition-colors dark:bg-slate-950/70 lg:flex lg:flex-col">
        {/* Logo */}
        <div className="flex items-center gap-3 px-2">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-primary text-primary-foreground">
            <Activity className="h-5 w-5" />
          </div>
          <div>
            <div className="text-sm font-semibold">{t("layout.appName")}</div>
            <div className="text-xs text-muted-foreground">{t("layout.appSubtitle")}</div>
          </div>
        </div>

        {/* Primary nav */}
        <nav className="mt-8 flex-1 space-y-1">
          {primaryNav.map((item) => (
            <NavLink
              key={item.href}
              to={item.href}
              end={item.href === "/"}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-all hover:bg-muted hover:text-foreground",
                  isActive && "bg-slate-900 text-white shadow-sm hover:bg-slate-900 hover:text-white dark:bg-white dark:text-slate-950",
                )
              }
            >
              <item.icon className="h-4 w-4" />
              {item.label}
            </NavLink>
          ))}

          {/* Divider */}
          <div className="my-3 border-t" />
          <p className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60">
            Developer
          </p>
          {devNav.map((item) => (
            <NavLink
              key={item.href}
              to={item.href}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-all hover:bg-amber-50 hover:text-amber-700 dark:hover:bg-amber-950/30 dark:hover:text-amber-400",
                  isActive && "bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300",
                )
              }
            >
              <item.icon className="h-4 w-4 text-amber-500" />
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <div className="lg:pl-64">
        <header className="sticky top-0 z-20 border-b bg-white/75 px-5 py-3 backdrop-blur-xl transition-colors dark:bg-slate-950/70">
          <div className="flex items-center justify-between gap-4">
            <div className="lg:hidden">
              <div className="text-sm font-semibold">Automation Ecosystem</div>
            </div>
            <div className="hidden text-sm text-muted-foreground lg:block">{t("layout.tagline")}</div>
            <div className="flex items-center gap-2 rounded-md border bg-card px-3 py-1.5 text-sm">
              <CheckCircle2 className={cn("h-4 w-4", ok ? "text-emerald-600" : "text-orange-500")} />
              {ok ? t("layout.healthy") : t("layout.needsAttention")}
            </div>
            <button
              onClick={toggleLanguage}
              title={i18n.language === "vi" ? t("language.en") : t("language.vi")}
              className="rounded-md border bg-card px-2.5 py-1.5 text-xs font-medium transition hover:bg-muted"
            >
              {i18n.language === "vi" ? "🇬🇧 EN" : "🇻🇳 VI"}
            </button>
            <ThemeToggle />
          </div>

          {/* Mobile nav */}
          <nav className="mt-3 flex gap-2 overflow-x-auto lg:hidden">
            {primaryNav.map((item) => (
              <NavLink
                key={item.href}
                to={item.href}
                end={item.href === "/"}
                className={({ isActive }) =>
                  cn(
                    "inline-flex h-8 shrink-0 items-center gap-2 rounded-md px-3 text-xs font-medium text-muted-foreground",
                    isActive ? "bg-slate-900 text-white" : "bg-muted",
                  )
                }
              >
                <item.icon className="h-3.5 w-3.5" />
                {item.label}
              </NavLink>
            ))}
          </nav>
        </header>
        <main className="mx-auto max-w-7xl px-5 py-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
