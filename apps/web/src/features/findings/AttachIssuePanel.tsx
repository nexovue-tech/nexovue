import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { TextField } from "@/shared/components/TextField";

import { useAttachIssue } from "./api";

const PROSE = "max-w-prose";
/** FR41: what attaching does, and does not, before the click. */
export const ATTACH_EXPLAINED =
  "Paste the address of an issue in a repository the GitHub connection reaches. nexovue reads its status every hour and shows it here; it never changes this case.";

/**
 * Attach an issue that already exists (FR40, design *Creating an issue*'s sibling): the
 * address is sent to nexovue, which parses it and fetches nothing. A refusal comes back
 * in the API's words: not an issue address, or outside the connected repositories.
 */
export function AttachIssuePanel({
  findingId,
  onAttached,
  onCancel,
}: {
  findingId: string;
  onAttached: () => void;
  onCancel: () => void;
}) {
  const [address, setAddress] = useState("");
  const attach = useAttachIssue(findingId);
  const pasted = address.trim();

  return (
    <ConfirmPanel label="Attach an issue">
      <p className={PROSE}>{ATTACH_EXPLAINED}</p>
      <form
        className="flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (pasted) attach.mutate(pasted, { onSuccess: () => onAttached() });
        }}
      >
        <TextField label="Issue address" name="issue" value={address} onChange={setAddress} />
        <div className="flex flex-wrap gap-2">
          <Button type="submit" pending={attach.isPending} pendingLabel="Attaching">
            Attach
          </Button>
          <Button variant="secondary" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </form>
      {attach.isError ? (
        <InlineFailure title="The issue was not attached" error={attach.error} />
      ) : null}
    </ConfirmPanel>
  );
}
