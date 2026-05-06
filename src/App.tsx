import { Navigate, Route, Routes } from "react-router-dom";
import { AppLayout } from "@/layouts/AppLayout";
import { DashboardPage } from "@/pages/DashboardPage";
import { JobsPage } from "@/pages/JobsPage";
import { JobDetailPage } from "@/pages/JobDetailPage";
import { TasksPage } from "@/pages/TasksPage";
import { TaskDetailPage } from "@/pages/TaskDetailPage";
import { CreateJobPage } from "@/pages/CreateJobPage";
import { SystemHealthPage } from "@/pages/SystemHealthPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { AccountsPage } from "@/pages/AccountsPage";
import { ArtifactsPage } from "@/pages/ArtifactsPage";
import { Toaster } from "@/components/ui/toast";

export default function App() {
  return (
    <>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
          <Route path="/tasks" element={<TasksPage />} />
          <Route path="/tasks/:taskId" element={<TaskDetailPage />} />
          <Route path="/create-job" element={<CreateJobPage />} />
          <Route path="/accounts" element={<AccountsPage />} />
          <Route path="/artifacts" element={<ArtifactsPage />} />
          <Route path="/system" element={<SystemHealthPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <Toaster />
    </>
  );
}
