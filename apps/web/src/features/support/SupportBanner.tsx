import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Button } from "@/shared/components/Button";
import { useSession } from "@/shared/identity";

import { endSupportView } from "./api";

const MINUTE = 60_000;

/** Whole minutes left, never below zero: "0 min left" reads as ending, not as broken. */
export function minutesLeft(until: string, now: number): number {
  return Math.max(0, Math.ceil((Date.parse(until) - now) / MINUTE));
}

/**
 * The strip on every screen while nexovue support views the console as someone (F31).
 *
 * Warning tones on purpose, unlike the verification offer: the person reading this is
 * not the person signed in, and nothing on screen may be changed. The API refuses any
 * write regardless; this says why before anyone tries.
 */
export function SupportBanner() {
  const session = useSession();
  const view = session.data?.support_view;
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!view) return;
    const timer = window.setInterval(() => setNow(Date.now()), 15_000);
    return () => window.clearInterval(timer);
  }, [view]);

  const end = useMutation({
    mutationKey: ["support-view", "end"],
    mutationFn: endSupportView,
    // The cookie is gone with the session; nothing here is signed in any more.
    onSuccess: () => window.location.assign("/login"),
  });

  if (!view || !session.data) return null;
  const person = session.data.user.name ?? session.data.user.email;

  return (
    <p
      role="status"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-warning-solid border-b-2 bg-warning-subtle px-4 py-1.5 text-warning-text text-xs"
    >
      <span>
        <span className="font-medium">Support view, read-only.</span> {view.staff} is viewing as{" "}
        {person}. {minutesLeft(view.until, now)} min left.
      </span>
      {end.isError ? <span>That did not go through. Try again.</span> : null}
      <Button
        variant="secondary"
        pending={end.isPending}
        pendingLabel="Ending…"
        onClick={() => end.mutate()}
      >
        End now
      </Button>
    </p>
  );
}
