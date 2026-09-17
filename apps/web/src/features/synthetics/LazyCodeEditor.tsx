import { lazy, Suspense } from "react";

import type { CodeLanguage } from "@/shared/components/CodeEditor";
import { SkeletonRows } from "@/shared/components/Skeleton";

/**
 * CodeMirror is about a third of a megabyte. Loaded when a body is first edited or
 * shown, so every other screen of the console does not pay for it.
 */
const CodeEditor = lazy(() =>
  import("@/shared/components/CodeEditor").then((module) => ({ default: module.CodeEditor })),
);

export function LazyCodeEditor(props: {
  label: string;
  value: string;
  onChange?: (value: string) => void;
  language: CodeLanguage;
  readOnly?: boolean;
  minLines?: number;
  maxLines?: number;
  error?: string;
}) {
  return (
    <Suspense fallback={<SkeletonRows rows={3} label={`Loading ${props.label}`} />}>
      <CodeEditor {...props} />
    </Suspense>
  );
}
