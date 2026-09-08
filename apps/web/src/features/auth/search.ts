/**
 * What the unauthenticated routes read out of the address bar.
 *
 * Parsing lives with the screens rather than in app/router.tsx, the same way the trace
 * filters do: the router declares that a route has search params, the feature decides
 * what shape they arrive in — and this can then be tested without a router.
 */

/**
 * The token on a recovery link, as the API's `auth/recovery.py::_link` writes it:
 * `{APP_BASE_URL}/verify-email?token=…`.
 */
export interface TokenSearch {
  /**
   * Absent when the address carries no usable token. A mail client that cut the link in
   * half, or somebody who typed the path directly — either way there is nothing to send,
   * and the screen says so rather than posting an empty token.
   */
  token?: string;
}

export function parseTokenSearch(search: Record<string, unknown>): TokenSearch {
  const token = search.token;
  return typeof token === "string" && token.length > 0 ? { token } : {};
}

/** What another screen can ask `/login` to say above the form. */
export const PASSWORD_WAS_RESET = "password-reset";

/**
 * Sign-out-everywhere ends this session too and clears the cookie, so the customer
 * arrives here having asked for exactly that. Without a notice the screen would look
 * identical to a session that quietly expired — the control working and the control
 * failing would be indistinguishable.
 */
export const SIGNED_OUT_EVERYWHERE = "signed-out-everywhere";

export type LoginNotice = typeof PASSWORD_WAS_RESET | typeof SIGNED_OUT_EVERYWHERE;

const RECOGNISED_NOTICES: ReadonlySet<string> = new Set<LoginNotice>([
  PASSWORD_WAS_RESET,
  SIGNED_OUT_EVERYWHERE,
]);

export interface LoginSearch {
  notice?: LoginNotice;
}

/**
 * Anything that is not a notice we recognise is dropped, so an address someone pasted
 * cannot make the sign-in screen say arbitrary words.
 */
export function parseLoginSearch(search: Record<string, unknown>): LoginSearch {
  const { notice } = search;
  return typeof notice === "string" && RECOGNISED_NOTICES.has(notice)
    ? { notice: notice as LoginNotice }
    : {};
}
