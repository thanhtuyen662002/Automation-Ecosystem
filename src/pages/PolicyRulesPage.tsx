import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "@/services/api";
import type { PolicyRule, ValidActionType } from "@/types/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toast } from "@/components/ui/toast";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { useToastError } from "@/hooks/useToastError";

const VALID_ACTION_TYPES: ValidActionType[] = ["publish_tiktok", "publish_youtube", "publish_facebook"];

const ACTION_TYPE_COLOR: Record<string, string> = {
  publish_tiktok: "running",
  publish_youtube: "failed",
  publish_facebook: "pending",
};

export function PolicyRulesPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  // ── Form state ─────────────────────────────────────────────────────────────
  const [actionType, setActionType] = useState<ValidActionType>("publish_tiktok");
  const [ruleName, setRuleName] = useState("");
  const [maxActions, setMaxActions] = useState("10");
  const [windowSeconds, setWindowSeconds] = useState("86400");
  const [cooldownSeconds, setCooldownSeconds] = useState("0");

  // ── Delete dialog ──────────────────────────────────────────────────────────
  const [deleteTarget, setDeleteTarget] = useState<PolicyRule | null>(null);

  // ── Query ──────────────────────────────────────────────────────────────────
  const rulesQuery = useQuery({
    queryKey: ["policy-rules"],
    queryFn: () => api.getPolicyRules({ limit: 200 }),
    refetchInterval: 10000,
  });

  const rules: PolicyRule[] = rulesQuery.data?.items ?? [];
  useToastError(rulesQuery.error, t("policyRules.errorLoad"));

  // ── Create mutation ────────────────────────────────────────────────────────
  const createMutation = useMutation({
    mutationFn: () =>
      api.createPolicyRule({
        action_type: actionType,
        rule_name: ruleName,
        max_actions: Number(maxActions),
        window_seconds: Number(windowSeconds),
        cooldown_seconds: Number(cooldownSeconds),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["policy-rules"] });
      setRuleName("");
      setMaxActions("10");
      setWindowSeconds("86400");
      setCooldownSeconds("0");
      toast({ title: t("policyRules.ruleAdded"), description: t("policyRules.ruleAddedDesc") });
    },
    onError: () => toast({ title: t("policyRules.errorCreate"), variant: "destructive" }),
  });

  // ── Delete mutation ────────────────────────────────────────────────────────
  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deletePolicyRule(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["policy-rules"] });
      setDeleteTarget(null);
      toast({ title: t("policyRules.ruleDeleted"), description: t("policyRules.ruleDeletedDesc") });
    },
    onError: () => toast({ title: t("policyRules.errorDelete"), variant: "destructive" }),
  });

  const canCreate =
    ruleName.trim().length > 0 &&
    Number(maxActions) >= 1 &&
    Number(windowSeconds) >= 60 &&
    !createMutation.isPending;

  return (
    <div className="space-y-6">
      <PageHeader title={t("policyRules.title")} description={t("policyRules.description")} />

      {/* ── Create rule form ─────────────────────────────────────────────── */}
      <Card>
        <CardContent className="p-5">
          <h3 className="font-semibold mb-4">{t("policyRules.addRule")}</h3>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground">{t("policyRules.labelActionType")}</label>
              <Select
                value={actionType}
                onChange={(e) => setActionType(e.target.value as ValidActionType)}
              >
                {VALID_ACTION_TYPES.map((a) => (
                  <option key={a} value={a}>{a}</option>
                ))}
              </Select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground">{t("policyRules.ruleName")}</label>
              <Input
                placeholder={t("policyRules.placeholderRuleName")}
                value={ruleName}
                onChange={(e) => setRuleName(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground">{t("policyRules.labelMaxActions")}</label>
              <Input
                type="number"
                min={1}
                value={maxActions}
                onChange={(e) => setMaxActions(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground">{t("policyRules.labelWindowSeconds")}</label>
              <Input
                type="number"
                min={60}
                value={windowSeconds}
                onChange={(e) => setWindowSeconds(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground">{t("policyRules.labelCooldown")}</label>
              <Input
                type="number"
                min={0}
                value={cooldownSeconds}
                onChange={(e) => setCooldownSeconds(e.target.value)}
              />
            </div>
            <div className="flex items-end">
              <Button disabled={!canCreate} onClick={() => createMutation.mutate()} className="w-full">
                <Plus className="h-4 w-4" />
                {t("policyRules.addRule")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* ── Rules table ───────────────────────────────────────────────────── */}
      <Card>
        <CardContent className="p-0">
          {rulesQuery.isLoading ? (
            <div className="space-y-3 p-5">
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
            </div>
          ) : rules.length === 0 ? (
            <EmptyState title={t("policyRules.noRules")} description={t("policyRules.noRulesDesc")} />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("policyRules.actionType")}</TableHead>
                  <TableHead>{t("policyRules.ruleName")}</TableHead>
                  <TableHead>{t("policyRules.maxActions")}</TableHead>
                  <TableHead>{t("policyRules.windowSeconds")}</TableHead>
                  <TableHead>{t("policyRules.cooldownSeconds")}</TableHead>
                  <TableHead>{t("policyRules.account")}</TableHead>
                  <TableHead className="text-right">{t("policyRules.actions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rules.map((rule) => (
                  <TableRow key={rule.id}>
                    <TableCell>
                      <Badge variant={ACTION_TYPE_COLOR[rule.action_type] as any ?? "default"}>
                        {rule.action_type}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-medium">{rule.rule_name}</TableCell>
                    <TableCell>{rule.max_actions ?? "—"}</TableCell>
                    <TableCell>{rule.window_seconds ?? "—"}s</TableCell>
                    <TableCell>{rule.cooldown_seconds}s</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {rule.account_id ? rule.account_id.slice(0, 8) + "…" : "global"}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setDeleteTarget(rule)}
                      >
                        <Trash2 className="h-3.5 w-3.5 text-destructive" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* ── Delete confirm ────────────────────────────────────────────────── */}
      <ConfirmDialog
        open={deleteTarget !== null}
        title={t("policyRules.deleteConfirmTitle")}
        description={`${t("policyRules.deleteConfirmDesc")} (${deleteTarget?.rule_name})`}
        confirmLabel={t("policyRules.deleteConfirm")}
        loading={deleteMutation.isPending}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
      />
    </div>
  );
}
