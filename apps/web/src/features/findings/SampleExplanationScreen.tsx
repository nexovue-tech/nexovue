import { useState } from "react";

import { SelectField } from "@/shared/components/SelectField";
import { ExplanationHeaderLine, ExplanationSection } from "./ExplanationSection";
import { headerLine } from "./explanation";
import {
  FIRST_WEEK,
  SAMPLE_CASES,
  type SampleCase,
  sampleExplanation,
  sampleFinding,
} from "./sample-explanation";

/**
 * `/findings/sample` (F3 FR46, AC84): what lines up with a finding, from fixed data,
 * labelled as a sample at the top and on each case. No API call: nothing here is read
 * from anyone's system. Reached from the empty findings feed, and from `/settings/ai`
 * once that exists (plan T4.7).
 */
export function SampleExplanationScreen() {
  const [which, setWhich] = useState<SampleCase>("likely");
  const now = Date.now();
  const finding = sampleFinding(now);
  const explanation = sampleExplanation(which, now);
  return (
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-y-auto px-4 py-5">
      <header className="flex flex-col gap-2">
        <p className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Sample</p>
        <h1 className="font-semibold font-serif text-ink text-xl tracking-tight">
          A sample finding: {finding.title}
        </h1>
        <p className="max-w-prose text-ink-dim text-sm">
          This is fixed sample data, not a reading of your system. It shows what a finding's page
          says about where to look first. {FIRST_WEEK}
        </p>
        <SelectField
          label="Show"
          value={which}
          options={SAMPLE_CASES}
          optionLabel={(choice) => choice.label}
          onChange={(id) => setWhich(id as SampleCase)}
        />
      </header>
      <ExplanationHeaderLine line={headerLine(explanation, { failed: false, finding })} />
      <ExplanationSection
        finding={finding}
        explanation={explanation}
        error={null}
        onRetry={() => {}}
        releaseRow={null}
        sample
      />
    </div>
  );
}
