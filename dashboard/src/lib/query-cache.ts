export const QUERY_CACHE_KEY = "releasi:linauto:rq:v1";

/** Drop the saved query cache (called on the login page, i.e. after logout or session expiry). */
export function clearPersistedQueryCache() {
  try {
    window.localStorage.removeItem(QUERY_CACHE_KEY);
  } catch {
    // storage blocked: nothing was saved
  }
}

const LINKEDIN_PROFILE_RE = /linkedin\.com\/in\/[^/?#\s]+/i;

/** True when the text is (or contains) a LinkedIn profile URL. */
export function isLinkedInProfileUrl(text: string): boolean {
  return LINKEDIN_PROFILE_RE.test(text.trim());
}
