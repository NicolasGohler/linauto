// Signed session-cookie helpers shared by middleware + auth route.
//
// Token format: `<userId>.<timestamp>.<sig>` where sig =
// base64(HMAC-SHA256(secret, "<userId>.<timestamp>")). userId is a UUID; the
// HMAC keeps clients from forging or mutating the id without the server
// secret. The middleware verifies expiry and signature; routes that need the
// caller's identity decode the userId portion after `verifyToken` succeeds.
//
// Sessions expire after 30 days. Rotating ``DASHBOARD_SECRET`` invalidates
// every outstanding cookie.

export const COOKIE_NAME = "releasi_session";
export const USER_COOKIE_NAME = "releasi_user";
export const SESSION_DURATION_MS = 30 * 24 * 60 * 60 * 1000;

function b64encode(bytes: Uint8Array): string {
  let str = "";
  for (let i = 0; i < bytes.length; i++) str += String.fromCharCode(bytes[i]);
  return btoa(str);
}

async function hmacSign(secret: string, payload: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  return b64encode(new Uint8Array(sig));
}

export async function signSessionToken(userId: string, secret: string): Promise<string> {
  const ts = Date.now();
  const payload = `${userId}.${ts}`;
  const sig = await hmacSign(secret, payload);
  return `${payload}.${sig}`;
}

export interface SessionClaims {
  userId: string;
  issuedAt: number;
}

export async function verifySessionToken(
  token: string,
  secret: string,
): Promise<SessionClaims | null> {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const [userId, tsStr, sig] = parts;
  if (!userId || !tsStr || !sig) return null;

  const ts = parseInt(tsStr, 10);
  if (Number.isNaN(ts)) return null;
  if (Date.now() - ts > SESSION_DURATION_MS) return null;

  const expected = await hmacSign(secret, `${userId}.${tsStr}`);
  // Constant-time compare via length-prefix check then byte equality
  if (expected.length !== sig.length) return null;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) {
    diff |= expected.charCodeAt(i) ^ sig.charCodeAt(i);
  }
  if (diff !== 0) return null;

  return { userId, issuedAt: ts };
}
