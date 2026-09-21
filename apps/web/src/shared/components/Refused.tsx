import { useSession } from "@/shared/identity";

/**
 * A screen this person's role cannot open, reached anyway — by a bookmark or a link
 * from someone else. Said plainly, with who can help, rather than shown as a failed
 * request (F26; `docs/design/principles.md` rule 3).
 *
 * **A support view is told the truth about itself.** Viewing as an owner, the
 * console offers a deliberately narrower set of actions than that owner holds —
 * nexovue support may not, for instance, grant itself more support access
 * (`auth/permissions.py`). Blaming the role for that read as nonsense: "Your
 * role (Owner) cannot open this", on the owner's own screen. Found by walking
 * the flow on 2026-09-21 (`docs/changes/console-review-fixes/`).
 */
export function Refused({ role, who }: { role: string | undefined; who: string }) {
  const viewing = useSession().data?.support_view != null;
  const named = role ? `Your role (${role[0]?.toUpperCase()}${role.slice(1)})` : "Your role";

  if (viewing) {
    return (
      <div role="status" className="border-line border-l-2 py-2 pl-3">
        <p className="font-medium text-ink text-sm">A support view cannot open this.</p>
        <p className="mt-1 text-ink-dim text-sm">
          It is read-only and narrower than the role it is viewing as, on purpose. End the view and
          sign in as yourself.
        </p>
      </div>
    );
  }

  return (
    <div role="status" className="border-line border-l-2 py-2 pl-3">
      <p className="font-medium text-ink text-sm">{named} cannot open this.</p>
      <p className="mt-1 text-ink-dim text-sm">Ask {who} if you need it.</p>
    </div>
  );
}
