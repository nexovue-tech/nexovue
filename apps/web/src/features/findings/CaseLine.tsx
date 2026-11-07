import { forwardRef } from "react";

import type { CaseView } from "./finding-page";
import { caseSentence } from "./finding-page";

/**
 * The page's one sentence stating the signal and the case together (FR23).
 * Focusable without being interactive: Take this, Resolve, Reopen and a closing
 * panel land focus here, so a screen reader hears the line that changed (AC67).
 */
export const CaseLine = forwardRef<HTMLParagraphElement, { view: CaseView }>(function CaseLine(
  { view },
  ref,
) {
  return (
    <p ref={ref} tabIndex={-1} data-testid="case-line" className="max-w-prose text-ink text-sm">
      {caseSentence(view)}
    </p>
  );
});
