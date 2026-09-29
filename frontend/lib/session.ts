// Phase 6: signing in is checked by the server (an HttpOnly session cookie this code can't read).
// What's left here is tidying the browser when someone signs out, so the next family member
// using this browser doesn't see the previous person's chat.

export function clearBrowserSession(): void {
  try {
    sessionStorage.clear();
  } catch {
    /* private mode */
  }
}
