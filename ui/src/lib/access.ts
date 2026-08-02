// Client side of the email gate: session storage, and the four endpoints that
// drive the login / approval / quota flow.
//
// Storage is wrapped in try/catch throughout, matching lib/theme.ts — a browser
// with storage blocked should degrade to "logged out every visit", never crash.

const SESSION_KEY = "tracerlens-session";
const EMAIL_KEY = "tracerlens-email";

/** Gate states, mirroring the `code` field the backend returns. */
export type AccessStatus =
  | "unknown"
  | "logged_out"
  | "pending"
  | "denied"
  | "link_sent"
  | "ok"
  | "limit_reached";

export interface AccessState {
  status: AccessStatus;
  email?: string;
  tokens_used: number;
  token_limit: number;
  extension_status: string;
}

export const EMPTY_ACCESS: AccessState = {
  status: "unknown",
  tokens_used: 0,
  token_limit: 0,
  extension_status: "none",
};

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // Storage blocked; the session simply won't survive this page.
  }
}

export function getSession(): string | null {
  return read(SESSION_KEY);
}

export function getStoredEmail(): string | null {
  return read(EMAIL_KEY);
}

export function storeSession(token: string, email: string): void {
  write(SESSION_KEY, token);
  write(EMAIL_KEY, email);
}

export function clearSession(): void {
  write(SESSION_KEY, null);
  write(EMAIL_KEY, null);
}

/**
 * Pull the one-time token out of a sign-in link, if this load came from one.
 *
 * The parameter is stripped from the URL immediately so the link doesn't
 * linger in the address bar, in a shared screenshot, or in the back history —
 * it is single-use server-side, but there's no reason to leave it lying around.
 */
export function takeAuthParam(): string | null {
  const params = new URLSearchParams(window.location.search);
  const auth = params.get("auth");
  if (!auth) return null;
  params.delete("auth");
  const query = params.toString();
  window.history.replaceState(
    {},
    "",
    window.location.pathname + (query ? `?${query}` : "") + window.location.hash,
  );
  return auth;
}
