import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api-client";

import { checkSecretsQueryKey, checkVariablesQueryKey, invalidateCheckLists } from "./api";
import type { CheckConfig } from "./check-model";

/**
 * Importing checks from a cURL command, an OpenAPI description or a Postman collection.
 *
 * Field names are the API's (`nexovue_api.checkimports.schemas`), unchanged. Parsing
 * happens only on the server: a second parser here would be a second definition of
 * what a file means, validated differently from a save.
 */

export const IMPORT_SOURCES = ["curl", "openapi", "postman"] as const;
export type ImportSource = (typeof IMPORT_SOURCES)[number];

export type PostmanFolderMode = "multistep" | "separate";

/** A credential the import found, or one a description declared without a value. */
export interface DetectedSecret {
  name: string;
  value: string;
  found_in: string;
}

export interface DetectedVariable {
  name: string;
  value: string;
}

/** Something the import read and did not carry over, with why. */
export interface SkippedItem {
  item: string;
  reason: string;
}

export interface ImportItem {
  key: string;
  name: string;
  method: string;
  url: string;
  config: CheckConfig;
  needs_values: string[];
  needs_files: string[];
  selected: boolean;
}

export interface ImportPlan {
  source: ImportSource;
  title: string;
  servers: string[];
  items: ImportItem[];
  secrets: DetectedSecret[];
  variables: DetectedVariable[];
  skipped: SkippedItem[];
}

export interface ImportPreviewInput {
  source: ImportSource;
  content: string;
  environment?: string | null;
  server_url?: string | null;
  postman_folders?: PostmanFolderMode;
}

export interface ImportedCheck {
  name: string;
  config: CheckConfig;
  interval_seconds: number;
  locations: string[];
}

export interface NamedValue {
  name: string;
  value: string;
}

export interface ImportConfirmBody {
  checks: ImportedCheck[];
  secrets: NamedValue[];
  variables: NamedValue[];
}

export interface ImportConfirmed {
  created: string[];
}

// A 500-operation description takes longer to parse, and to create, than a screen read.
const IMPORT_TIMEOUT_MS = 60_000;

export function usePreviewImport(projectId: string | undefined) {
  return useMutation({
    mutationKey: ["check-imports", "preview"],
    // The pasted source and the credentials found in it: drop them once nothing is watching.
    gcTime: 0,
    mutationFn: (input: ImportPreviewInput) =>
      apiClient.post<ImportPlan>(
        "check-imports/preview",
        { project_id: projectId, ...input },
        { timeoutMs: IMPORT_TIMEOUT_MS },
      ),
  });
}

export function useConfirmImport(projectId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["check-imports", "confirm"],
    // What it sends carries the found secrets' values in plaintext.
    gcTime: 0,
    mutationFn: (body: ImportConfirmBody) =>
      apiClient.post<ImportConfirmed>(
        "check-imports/confirm",
        { project_id: projectId, ...body },
        { timeoutMs: IMPORT_TIMEOUT_MS },
      ),
    onSuccess: () =>
      Promise.all([
        invalidateCheckLists(queryClient, projectId),
        queryClient.invalidateQueries({ queryKey: checkSecretsQueryKey(projectId ?? "") }),
        queryClient.invalidateQueries({ queryKey: checkVariablesQueryKey(projectId ?? "") }),
      ]),
  });
}
