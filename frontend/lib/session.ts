// Phase 3 "sign in": remembers, for this browser tab, that you chose to continue as the vault's
// owner. It is a convenience screen, not security: the server does not check it. Real sign-in
// (passwords, one account per family member, enforced by the server) arrives in Phase 6.

const KEY = "vault-session";

export function getSession(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function startSession(owner: string): void {
  try {
    sessionStorage.setItem(KEY, owner);
  } catch {
    /* private mode: the session simply isn't remembered */
  }
}

export function endSession(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
