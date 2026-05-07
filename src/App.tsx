import { Navigate, Route, Routes } from "react-router-dom";
import { AppLayout } from "@/layouts/AppLayout";
import { DashboardPage } from "@/pages/DashboardPage";
import { JobsPage } from "@/pages/JobsPage";
import { JobDetailPage } from "@/pages/JobDetailPage";
import { TasksPage } from "@/pages/TasksPage";
import { TaskDetailPage } from "@/pages/TaskDetailPage";
import { SystemHealthPage } from "@/pages/SystemHealthPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { AccountsPage } from "@/pages/AccountsPage";
// ── New UX pages ──────────────────────────────────────────────────────────────
import { AutomationsPage } from "@/pages/AutomationsPage";
import { AutomationWizardPage } from "@/pages/AutomationWizardPage";
import { AdvancedWorkflowBuilderPage } from "@/pages/AdvancedWorkflowBuilderPage";
import { ContentLibraryPage } from "@/pages/ContentLibraryPage";
import { PostingLimitsPage } from "@/pages/PostingLimitsPage";
import { AccountBrainPage } from "@/pages/AccountBrainPage";
import { Toaster } from "@/components/ui/toast";

export default function App() {
  return (
    <>
      <Routes>
        <Route element={<AppLayout />}>
          {/* ── Core ───────────────────────────────────────────────────────── */}
          <Route path="/" element={<DashboardPage />} />

          {/* ── User-facing (new UX) ────────────────────────────────────── */}
          <Route path="/automations" element={<AutomationsPage />} />
          <Route path="/automations/create" element={<AutomationWizardPage />} />
          <Route path="/accounts" element={<AccountsPage />} />
          <Route path="/content" element={<ContentLibraryPage />} />
          <Route path="/posting-limits" element={<PostingLimitsPage />} />
          <Route path="/account-brain" element={<AccountBrainPage />} />

          {/* ── Developer / legacy ──────────────────────────────────────── */}
          <Route path="/advanced/workflow-builder" element={<AdvancedWorkflowBuilderPage />} />
          <Route path="/create-job" element={<AdvancedWorkflowBuilderPage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
          <Route path="/tasks" element={<TasksPage />} />
          <Route path="/tasks/:taskId" element={<TaskDetailPage />} />
          <Route path="/system" element={<SystemHealthPage />} />
          <Route path="/settings" element={<SettingsPage />} />

          {/* ── Legacy URL redirects ─────────────────────────────────────── */}
          <Route path="/artifacts" element={<Navigate to="/content" replace />} />
          <Route path="/policy-rules" element={<Navigate to="/posting-limits" replace />} />
          <Route path="/create" element={<Navigate to="/automations" replace />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <Toaster />
    </>
  );
}
