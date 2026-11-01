import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { Button } from "@/shared/components/Button";
import { ConfirmPanel } from "@/shared/components/ConfirmPanel";
import { InlineFailure } from "@/shared/components/InlineFailure";
import { SelectField } from "@/shared/components/SelectField";
import type { Quieting, ServiceDetection } from "@/shared/detection/api";
import { useQuietService } from "@/shared/detection/api";
import { DetectionBadge } from "@/shared/detection/DetectionBadge";
import { QUIET_CHOICES, quietDone, quietEffect } from "@/shared/detection/quiet";
import { formatZonedTime } from "@/shared/format/zoned";
import { useCan } from "@/shared/identity";

import { describeFindingsInWindow } from "./service-detail";

const LINK = "font-medium text-brand-text hover:underline";

/**
 * A service's detection at a glance (design, *The service screen*): its state in a
 * word, its snooze and mute beside it, and the findings in the window, which the
 * charts shade. A snooze is every working role's (D25); a mute is an admin's (FR23).
 */
export function DetectionStrip({
  projectId,
  serviceName,
  detection,
  failed,
  zone,
}: {
  projectId: string;
  serviceName: string;
  detection: ServiceDetection | undefined;
  failed: boolean;
  /** The project's IANA zone; the browser's until it is read. */
  zone?: string;
}) {
  const canSnooze = useCan("operate_alerts");
  const canMute = useCan("configure_detection");
  const quiet = useQuietService(projectId, serviceName);
  const [arming, setArming] = useState<Quieting>();
  const [choice, setChoice] = useState("");
  const [done, setDone] = useState("");

  const choices = arming === undefined ? [] : QUIET_CHOICES[arming];
  const chosen = choices.find((each) => each.id === choice) ?? choices[0];
  const arm = (kind: Quieting) => {
    setDone("");
    setArming(kind);
    setChoice(QUIET_CHOICES[kind][0]?.id ?? "");
  };
  const set = (kind: Quieting, minutes: number | null) => {
    setDone("");
    quiet.mutate(
      { kind, minutes },
      {
        onSuccess: (controls) => {
          setArming(undefined);
          setDone(quietDone(kind, serviceName, controls, zone));
        },
      },
    );
  };
  const ending = (kind: Quieting) =>
    quiet.isPending && quiet.variables?.kind === kind && quiet.variables.minutes === null;

  return (
    <section
      aria-label="Detection"
      className="flex flex-col gap-2 border-line-subtle border-b py-2 text-sm"
    >
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="font-mono text-2xs text-ink-dim uppercase tracking-caps">Detection</span>
        <DetectionBadge detection={detection} failed={failed} zone={zone} />
        {detection?.snoozed_until ? (
          <span className="text-ink-dim">
            Snoozed until {formatZonedTime(detection.snoozed_until, { zone })}
            {canSnooze ? (
              <>
                {" "}
                <LinkButton
                  pending={ending("snooze")}
                  pendingLabel="Ending the snooze"
                  onClick={() => set("snooze", null)}
                >
                  End snooze
                </LinkButton>
              </>
            ) : null}
          </span>
        ) : canSnooze && detection ? (
          <LinkButton onClick={() => arm("snooze")}>Snooze</LinkButton>
        ) : null}
        {canMute && detection ? (
          detection.muted_until ? (
            <LinkButton
              pending={ending("mute")}
              pendingLabel="Unmuting"
              onClick={() => set("mute", null)}
            >
              Unmute
            </LinkButton>
          ) : (
            <LinkButton onClick={() => arm("mute")}>Mute</LinkButton>
          )
        ) : null}
      </div>

      {arming && chosen ? (
        <ConfirmPanel label={arming === "snooze" ? "Snooze" : "Mute"}>
          <p className="max-w-prose text-ink">{quietEffect(arming, serviceName)}</p>
          <div className="flex flex-wrap items-end gap-3">
            <div className="w-40">
              <SelectField
                label="For"
                value={chosen.id}
                options={choices}
                onChange={setChoice}
                optionLabel={(each) => each.label}
                stacked
              />
            </div>
            <Button
              pending={quiet.isPending}
              pendingLabel="Saving"
              onClick={() => set(arming, chosen.minutes)}
            >
              {arming === "snooze" ? `Snooze ${serviceName}` : `Mute ${serviceName}`}
            </Button>
            <Button variant="secondary" onClick={() => setArming(undefined)}>
              Cancel
            </Button>
          </div>
        </ConfirmPanel>
      ) : null}
      {/* Always in the page, so a screen reader hears what changed when it does. */}
      <p role="status" className="text-ink">
        {done}
      </p>
      {quiet.error ? <InlineFailure title="That did not go through" error={quiet.error} /> : null}

      {detection ? (
        <p className="text-ink-dim">
          {describeFindingsInWindow(detection.findings, detection.truncated)}
          {detection.findings.map((marker) => (
            <span key={marker.id}>
              {" "}
              <Link to="/findings/$findingId" params={{ findingId: marker.id }} className={LINK}>
                {formatZonedTime(marker.started_at, { zone })}
                {marker.closed_at ? "" : " (open)"}
              </Link>
            </span>
          ))}
        </p>
      ) : null}
    </section>
  );
}

/** A quiet action in the strip's line, which says while it works that it is working. */
function LinkButton({
  children,
  pending = false,
  pendingLabel,
  onClick,
}: {
  children: string;
  pending?: boolean;
  pendingLabel?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`${LINK} disabled:cursor-not-allowed disabled:opacity-60`}
      disabled={pending}
      aria-busy={pending || undefined}
      onClick={onClick}
    >
      {pending && pendingLabel ? pendingLabel : children}
    </button>
  );
}
