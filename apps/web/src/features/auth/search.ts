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

/** The one thing another screen can ask `/login` to say above the form. */
export const PASSWORD_WAS_RESET = "password-reset";

export interface LoginSearch {
  notice?: typeof PASSWORD_WAS_RESET;
}

/**
 * Anything that is not the one notice we recognise is dropped, so an address someone
 * pasted cannot make the sign-in screen say arbitrary words.
 */
export function parseLoginSearch(search: Record<string, unknown>): LoginSearch {
  return search.notice === PASSWORD_WAS_RESET ? { notice: PASSWORD_WAS_RESET } : {};
}
