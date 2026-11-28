import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

/** The rules that may name a cause, by their stable names (F3 design, *The rules*). */
export type RuleName =
  | "signed_deploy"
  | "dependency_finding"
  | "signed_deploy_weak"
  | "dependency_deploy"
  | "dependency_edge"
  | "traffic_rose"
  | "project_deploy"
  | "project_shared_dependency";

/** One rule's numbers: counts only, never a name (FR54). */
export interface RuleCount {
  rule: RuleName;
  named: number;
  useful: number;
  wrong_cause: number;
  not_useful: number;
  bad_wording: number;
  stopped: boolean;
}

export interface RuleCounts {
  rules: RuleCount[];
  without_cause_useful: number;
  without_cause_not_useful: number;
}

const RULE_COUNTS_KEY = ["explanations", "rule-counts"] as const;

/** `GET /explanations/rule-counts`: Postgres only; for `configure_detection`. */
export function useRuleCounts(enabled: boolean) {
  return useQuery({
    queryKey: RULE_COUNTS_KEY,
    enabled,
    queryFn: ({ signal }) => apiClient.get<RuleCounts>("explanations/rule-counts", { signal }),
  });
}

/** Stop a rule naming causes in this organization, or let it again. Audited. */
export function useSetRuleStopped() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ rule, stopped }: { rule: RuleName; stopped: boolean }) =>
      stopped
        ? apiClient.put<{ rule: RuleName; stopped: boolean }>(`explanations/stopped-rules/${rule}`)
        : apiClient.delete<{ rule: RuleName; stopped: boolean }>(
            `explanations/stopped-rules/${rule}`,
          ),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: RULE_COUNTS_KEY }),
  });
}
