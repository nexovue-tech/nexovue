/**
 * A screen this person's role cannot open, reached anyway — by a bookmark or a link
 * from someone else. Said plainly, with who can help, rather than shown as a failed
 * request (F26; `docs/design/principles.md` rule 3).
 */
export function Refused({ role, who }: { role: string | undefined; who: string }) {
  const named = role ? `Your role (${role[0]?.toUpperCase()}${role.slice(1)})` : "Your role";
  return (
    <div role="status" className="border-line border-l-2 py-2 pl-3">
      <p className="font-medium text-ink text-sm">{named} cannot open this.</p>
      <p className="mt-1 text-ink-dim text-sm">Ask {who} if you need it.</p>
    </div>
  );
}
