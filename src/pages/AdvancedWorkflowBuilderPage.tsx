import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ArrowLeft } from "lucide-react";
import { CreateJobPage } from "@/pages/CreateJobPage";

export function AdvancedWorkflowBuilderPage() {
  const { t } = useTranslation();

  return (
    <div className="space-y-6">
      {/* ── Developer warning banner ──────────────────────────────────────── */}
      <div className="flex items-start gap-3 rounded-xl border border-amber-300 bg-amber-50 p-4 dark:border-amber-700 dark:bg-amber-950/30">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600 dark:text-amber-400" />
        <div className="flex-1 min-w-0">
          <p className="font-semibold text-amber-900 dark:text-amber-200">
            {t("advancedBuilder.warningTitle")}
          </p>
          <p className="mt-1 text-sm text-amber-700 dark:text-amber-300">
            {t("advancedBuilder.warningDesc")}
          </p>
        </div>
        <Link
          to="/automations"
          className="shrink-0 inline-flex items-center gap-1.5 rounded-md border border-amber-300 px-3 py-1.5 text-xs font-medium hover:bg-amber-100 dark:border-amber-700 dark:hover:bg-amber-950/40 transition-colors"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          {t("advancedBuilder.switchToWizard")}
        </Link>
      </div>

      {/* ── Existing CreateJobPage (unchanged) ───────────────────────────── */}
      <CreateJobPage />
    </div>
  );
}
