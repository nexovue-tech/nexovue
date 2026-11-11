import { Link } from "@tanstack/react-router";

import { useCan } from "@/shared/identity";

import { useConnections } from "./api";
import { NEEDS_ATTENTION_BANNER } from "./connection-words";

/**
 * Across the console, to owners and admins, while a connection needs a person (FR31). Not
 * dismissible: the state is still true until someone fixes it. Others see nothing, and no
 * request is made for them.
 */
export function ConnectionsBanner() {
  const allowed = useCan("manage_connections");
  const connections = useConnections(allowed);
  const attention = connections.data?.connections.some(
    (connection) => connection.state === "needs_attention",
  );
  if (!allowed || !attention) return null;

  return (
    <p
      role="status"
      className="border-warning-solid border-b-2 bg-warning-subtle px-4 py-1.5 text-warning-text text-xs"
    >
      <Link to="/settings/connections" className="underline">
        {NEEDS_ATTENTION_BANNER}
      </Link>
    </p>
  );
}
