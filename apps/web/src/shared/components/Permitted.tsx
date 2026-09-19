import type { ReactNode } from "react";

import type { Action } from "../identity";
import { useCan, useSession } from "../identity";
import { Refused } from "./Refused";

/**
 * A whole screen that only some roles may use, reached anyway by a bookmark or a
 * shared link (F26). Renders the screen, or says plainly that this role cannot open
 * it — never a form that fails when submitted.
 */
export function Permitted({
  action,
  who,
  children,
}: {
  action: Action;
  /** Who can help, in words: "an owner or an admin". */
  who: string;
  children: ReactNode;
}) {
  const session = useSession();
  const allowed = useCan(action);
  if (session.isPending) return null;
  if (allowed) return <>{children}</>;
  return (
    <div className="px-4 pt-5">
      <Refused role={session.data?.user.role} who={who} />
    </div>
  );
}
