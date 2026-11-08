import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";
import { formatRelativeTime } from "@/shared/format/time";

/**
 * A project's deploy intake (F28 FR1, Step 2): its secrets, what last arrived, and the
 * CI recipes that sign for it. Field names are the API's (`deploys/schemas.py`).
 *
 * **A secret's value is in one response only**, the one that minted it (FR2, AC4).
 * `DeploySecret` has no value field, so a list cannot show one.
 */

/** FR4: each request is checked against every live secret, so a project holds few. */
export const MAX_DEPLOY_SECRETS = 5;
/** Poll while the section is open, for at most ten minutes (design, *Project settings*). */
export const POLL_EVERY_MS = 5_000;
export const POLL_FOR_MS = 10 * 60 * 1_000;

export interface DeploySecret {
  id: string;
  /** The secret's last characters, so one can be told from another. */
  hint: string;
  created_at: string;
  last_used_at: string | null;
}

export interface MintedDeploySecret extends DeploySecret {
  secret: string;
}

/** The last refusal a signed sender earned; an unsigned one is never recorded (D22). */
export interface LastRefusal {
  at: string;
  position: number | null;
  field: string | null;
  secret_id: string | null;
}

export interface CurrentVersion {
  service_name: string;
  version: string;
  first_seen_at: string | null;
}

export interface DeploySettings {
  project_id: string;
  webhook_path: string;
  signature_header: string;
  secrets: DeploySecret[];
  last_deploy_at: string | null;
  last_check_at: string | null;
  last_refusal: LastRefusal | null;
  current_versions: CurrentVersion[];
  over_release_cap: boolean;
}

export interface MintedDeploySettings extends DeploySettings {
  minted: MintedDeploySecret;
}

export interface WithdrawInput {
  secretId: string;
  /** Also remove what this secret sent from this instant on (D37). */
  removeSince?: string;
}

const deploySettingsKey = (projectId: string) => ["deploy-settings", projectId] as const;

export function useDeploySettings(projectId: string, openedAt: number) {
  return useQuery({
    queryKey: deploySettingsKey(projectId),
    queryFn: ({ signal }) =>
      apiClient.get<DeploySettings>("deploys/webhook-secrets", {
        searchParams: { project_id: projectId },
        signal,
      }),
    refetchInterval: () => (Date.now() - openedAt < POLL_FOR_MS ? POLL_EVERY_MS : false),
  });
}

export function useMintDeploySecret(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["deploy-secrets", "mint"],
    // The answer carries the plaintext: keep it no longer than something watches it.
    gcTime: 0,
    mutationFn: () =>
      apiClient.post<MintedDeploySettings>("deploys/webhook-secrets", undefined, {
        searchParams: { project_id: projectId },
      }),
    onSuccess: (minted) => {
      const { minted: _secret, ...listed } = minted;
      queryClient.setQueryData(deploySettingsKey(projectId), listed);
    },
  });
}

export function useWithdrawDeploySecret(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["deploy-secrets", "withdraw"],
    mutationFn: ({ secretId, removeSince }: WithdrawInput) =>
      apiClient.delete<void>(`deploys/webhook-secrets/${secretId}`, {
        searchParams: { remove_since: removeSince },
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: deploySettingsKey(projectId) }),
  });
}

/** The address CI posts to: this console's own origin, which serves the API too. */
export function webhookUrl(path: string, origin: string): string {
  return `${origin}${path}`;
}

/** "Item 3, field `url`, 2 minutes ago", or the whole body's refusal. */
export function refusalSentence(refusal: LastRefusal, now = Date.now()): string {
  const where = [
    refusal.position === null ? null : `Item ${refusal.position}`,
    refusal.field === null ? null : `field \`${refusal.field}\``,
  ].filter((part) => part !== null);
  const what = where.length > 0 ? where.join(", ") : "The whole body";
  return `${what}, ${formatRelativeTime(refusal.at, now)}`;
}

export type RecipeKind = "github" | "gitlab" | "curl";

/**
 * The signing step every recipe shares (FR14), in plain POSIX shell with `python3` for
 * the HMAC, so the secret is read from the environment and never passed as an argument.
 * It never fails the deploy: errors do not stop it, each attempt has 10 seconds and the
 * retries 30 in all, a refusal is a warning, and it exits 0 (BR13). The
 * same text is on `docs/product/setup/deploys.md`, whose recipes
 * `test_deploy_recipes.py` runs against the API.
 */
function signingStep(url: string): string {
  return `set +e
body=$(printf '{"dry_run":%s,"deploys":[{"delivery_id":"%s","service_name":"%s","version":"%s","deployed_at":"%s","commit_sha":"%s","url":"%s"}]}' \\
  "\${NEXOVUE_DRY_RUN:-false}" "$NEXOVUE_DELIVERY_ID" "$NEXOVUE_SERVICE" "$NEXOVUE_VERSION" \\
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$NEXOVUE_COMMIT" "$NEXOVUE_COMMIT_URL")
signature=$(printf '%s' "$body" | python3 -c 'import hashlib, hmac, os, sys; print(hmac.new(os.environ["NEXOVUE_DEPLOY_SECRET"].encode(), sys.stdin.buffer.read(), hashlib.sha256).hexdigest())')
status=$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 \\
  --retry 2 --retry-connrefused --retry-max-time 30 --header 'Content-Type: application/json' \\
  --header "X-Hub-Signature-256: sha256=$signature" --data "$body" \\
  "${url}")
case "$status" in
  2??) echo "nexovue: deploy reported (HTTP $status)" ;;
  *) echo "::warning::nexovue did not record this deploy (HTTP $status); the deploy itself is unaffected" ;;
esac
exit 0`;
}

export function recipe(kind: RecipeKind, url: string): string {
  const step = signingStep(url);
  if (kind === "github") {
    return `- name: Report the deploy to nexovue
  env:
    NEXOVUE_DEPLOY_SECRET: \${{ secrets.NEXOVUE_DEPLOY_SECRET }}
    NEXOVUE_SERVICE: checkout
    # The same value your build passes as service.version.
    NEXOVUE_VERSION: \${{ github.sha }}
    NEXOVUE_DELIVERY_ID: checkout-\${{ github.run_id }}-\${{ github.run_attempt }}
    NEXOVUE_COMMIT: \${{ github.sha }}
    NEXOVUE_COMMIT_URL: \${{ github.server_url }}/\${{ github.repository }}/commit/\${{ github.sha }}
    # Remove once the dry run shows as "last check received".
    NEXOVUE_DRY_RUN: "true"
  run: |
${indent(step, 4)}`;
  }
  if (kind === "gitlab") {
    return `report-deploy:
  stage: .post
  variables:
    NEXOVUE_SERVICE: checkout
    # The same value your build passes as service.version.
    NEXOVUE_VERSION: $CI_COMMIT_SHA
    NEXOVUE_DELIVERY_ID: checkout-$CI_JOB_ID
    NEXOVUE_COMMIT: $CI_COMMIT_SHA
    NEXOVUE_COMMIT_URL: $CI_PROJECT_URL/-/commit/$CI_COMMIT_SHA
    # Remove once the dry run shows as "last check received".
    NEXOVUE_DRY_RUN: "true"
  script:
    - |
${indent(step, 6)}`;
  }
  return `# NEXOVUE_DEPLOY_SECRET comes from your secret store, never from this file.
export NEXOVUE_SERVICE=checkout
export NEXOVUE_VERSION="$BUILD_VERSION"   # the same value as service.version
export NEXOVUE_DELIVERY_ID="checkout-$BUILD_ID-$BUILD_ATTEMPT"
export NEXOVUE_COMMIT="$GIT_COMMIT"
export NEXOVUE_COMMIT_URL="https://git.example.com/acme/checkout/commit/$GIT_COMMIT"
export NEXOVUE_DRY_RUN=true   # remove once the dry run shows as "last check received"
${step}`;
}

function indent(text: string, spaces: number): string {
  const pad = " ".repeat(spaces);
  return text
    .split("\n")
    .map((line) => `${pad}${line}`)
    .join("\n");
}
