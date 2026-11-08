import { useNavigate } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";

import { ApiError } from "@/shared/api-client";
import { Button } from "@/shared/components/Button";
import { TextField } from "@/shared/components/TextField";

import type { DashboardDocument } from "./api";
import { useCreateDashboard } from "./api";

/**
 * Name a starting point and save it (F20, Step 2). Opening a template writes nothing
 * (AC38): the row exists only once this is saved. A title already in use is refused by
 * the API, ignoring case, and its words are shown here (BR12, AC11).
 */
export function TitleDialog({
  document,
  onCancel,
}: {
  document: DashboardDocument;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(document.title);
  const create = useCreateDashboard();
  const navigate = useNavigate();
  const refusal =
    create.error instanceof ApiError
      ? (create.error.problem?.detail ?? "Could not save this dashboard.")
      : undefined;

  const save = (event: FormEvent) => {
    event.preventDefault();
    create.mutate(
      { ...document, title: title.trim() },
      {
        onSuccess: (saved) =>
          void navigate({ to: "/dashboards/$dashboardId", params: { dashboardId: saved.id } }),
      },
    );
  };

  return (
    <form
      onSubmit={save}
      aria-label="Save a new dashboard"
      className="flex flex-col gap-3 rounded-sm border border-line p-4"
    >
      <TextField label="Title" name="title" value={title} onChange={setTitle} error={refusal} />
      <div className="flex gap-2">
        <Button type="submit" pending={create.isPending} pendingLabel="Saving">
          Save
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
