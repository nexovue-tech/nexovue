import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { SelectField } from "@/shared/components/SelectField";

import type { EmailOutcome, FindingDetail } from "./api";
import { useAssignableMembers, useCaseChange } from "./api";

/** The picker's explicit nobody, so clearing an assignment is a choice (FR4). */
const NOBODY = "nobody";

/**
 * Hand the case over (FR4, AC132): a stacked select with a *Nobody* option and
 * an **Assign** button. Choosing alone does nothing — a native select fires on a
 * phone-picker slip — and the call carries what the page last showed, so a
 * changed assignee answers 409 rather than silently overwriting.
 */
export function ReassignPanel({
  finding,
  root,
  onDone,
  onConflict,
  onCancel,
}: {
  finding: FindingDetail;
  root: FindingDetail;
  onDone: (assigneeName: string | null, outcome: EmailOutcome) => void;
  onConflict: (detail: string) => void;
  onCancel: () => void;
}) {
  const members = useAssignableMembers(true);
  const change = useCaseChange(finding.id);
  const [choice, setChoice] = useState(root.assignee_id ?? NOBODY);

  const options = [
    { id: NOBODY, label: "Nobody" },
    ...(members.data ?? []).map((member) => ({
      id: member.id,
      label: member.name ?? member.email,
    })),
  ];

  const assign = () => {
    const assignee = choice === NOBODY ? null : choice;
    change.mutate(
      { assignee_id: assignee, expected_assignee_id: root.assignee_id ?? null },
      {
        onSuccess: (changed) => {
          const name = options.find((option) => option.id === choice)?.label ?? null;
          onDone(assignee === null ? null : name, changed.email_outcome);
        },
        onError: (error) => {
          const detail =
            error instanceof Error && "problem" in error
              ? ((error as { problem?: { detail?: string } }).problem?.detail ?? error.message)
              : "That did not go through.";
          onConflict(detail);
        },
      },
    );
  };

  return (
    <ConfirmPanel label="Reassign">
      <div className="max-w-xs">
        <SelectField
          label="Assign to"
          value={choice}
          options={options}
          onChange={setChoice}
          optionLabel={(option) => option.label}
          stacked
        />
      </div>
      <div className="flex gap-2">
        <Button pending={change.isPending} pendingLabel="Assigning" onClick={assign}>
          Assign
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </ConfirmPanel>
  );
}
