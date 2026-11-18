"use client";

import { useState, useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { clearPersistedQueryCache } from "@/lib/query-cache";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Wordmark } from "@/components/layout/brand-mark";

function LoginForm() {
  const params = useSearchParams();
  // Landing here means logged out or session expired: drop any saved data.
  useEffect(() => { clearPersistedQueryCache(); }, []);
  // Only allow same-site relative paths (blocks open redirects via ?next=).
  const rawNext = params.get("next") ?? "/";
  const next = rawNext.startsWith("/") && !rawNext.startsWith("//") ? rawNext : "/";

  const [handle, setHandle] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");

    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ handle: handle.trim(), password }),
    });

    if (res.ok) {
      // Full navigation, not router.replace(): the client router can reuse a cached
      // redirect-to-/login from before the cookie existed, which bounced users back
      // to the sign-in form and forced a second login.
      window.location.assign(next);
      return;
    }

    const body = await res.json().catch(() => ({}));
    if (res.status === 503) {
      setError(body.error ?? "No users provisioned. Bootstrap on the server first.");
    } else {
      setError(body.error ?? "Incorrect handle or password");
    }
    setLoading(false);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background p-3">
      <div className="bg-forest-stage relative flex h-full w-full items-center justify-center overflow-hidden rounded-[24px] px-4">
        <div className="w-full max-w-sm space-y-6 rounded-[18px] border border-white/10 bg-card p-7 shadow-float">
          <div className="space-y-2">
            <Wordmark className="text-[26px] text-foreground" markClassName="h-7 w-7" />
            <p className="text-sm text-muted-foreground">Sign in to the outreach dashboard</p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-3">
            <Input
              type="text"
              value={handle}
              onChange={(e) => setHandle(e.target.value)}
              placeholder="Handle"
              autoFocus
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              required
              className="h-10"
            />
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              required
              className="h-10"
            />

            {error && (
              <p className="text-sm text-danger">{error}</p>
            )}

            <Button
              type="submit"
              size="lg"
              disabled={loading || !handle || !password}
              className="w-full"
            >
              {loading ? "Signing in…" : "Sign in"}
            </Button>
          </form>

          <p className="text-xs text-muted-foreground">
            Stays signed in for 30 days
          </p>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
