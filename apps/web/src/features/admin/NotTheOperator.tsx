import { EmptyState } from "@/shared/components/EmptyState";

/**
 * What everyone but nexovue's operator sees at a platform address.
 *
 * A refusal rather than a redirect: a customer who reaches this typed it or
 * followed a stale link, and sending them somewhere else silently would leave
 * them wondering what they missed. The API refuses regardless — this only keeps
 * the screen honest about why it is empty.
 */
export function NotTheOperator() {
  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-10">
      <EmptyState
        title="This is nexovue's own area"
        detail="Only nexovue's operator can open it. Nothing about your organization lives here."
      />
    </div>
  );
}
