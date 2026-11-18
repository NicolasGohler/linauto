"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { testProxyUnsaved, testProxyStored } from "@/lib/api";
import type { ProxyTestResult } from "@/lib/types";

/**
 * Proxy form state. `password` is a local-only field — the server never sends
 * the stored password back, and in edit mode we only submit it when the user
 * explicitly rotates it (tracked via `passwordEditing`).
 */
export interface ProxyFormValue {
  host: string;
  port: string; // stored as string in form, coerced to number on submit
  username: string;
  password: string;
  country: string;
}

export const emptyProxyForm: ProxyFormValue = {
  host: "",
  port: "",
  username: "",
  password: "",
  country: "",
};

interface ProxySettingsProps {
  value: ProxyFormValue;
  onChange: (v: ProxyFormValue) => void;
  /**
   * Edit mode: a password is already stored on the server. The password input
   * is masked until the user clicks "Change". In create mode, pass `false` so
   * the password field is always visible.
   */
  hasStoredPassword: boolean;
  passwordEditing: boolean;
  onPasswordEditingChange: (editing: boolean) => void;
  /**
   * If provided, the Test button will test the *stored* credentials for this
   * account. Otherwise (new account form) it tests the form values directly.
   */
  accountId?: string;
}

export function ProxySettings({
  value,
  onChange,
  hasStoredPassword,
  passwordEditing,
  onPasswordEditingChange,
  accountId,
}: ProxySettingsProps) {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<ProxyTestResult | null>(null);

  const set = <K extends keyof ProxyFormValue>(key: K, v: ProxyFormValue[K]) =>
    onChange({ ...value, [key]: v });

  async function handleTest() {
    setTesting(true);
    setResult(null);
    try {
      let res: ProxyTestResult;
      // Use stored credentials when we're editing an existing account AND the
      // user hasn't modified fields in a way that would require the form
      // values. For simplicity: test stored if accountId is set and password
      // isn't being edited; otherwise test form values.
      if (accountId && !passwordEditing) {
        res = await testProxyStored(accountId);
      } else {
        if (!value.host || !value.port) {
          setResult({ ok: false, error: "Host and port are required" });
          return;
        }
        res = await testProxyUnsaved({
          proxy_host: value.host.trim(),
          proxy_port: Number(value.port),
          proxy_username: value.username.trim() || null,
          proxy_password: value.password || null,
        });
      }
      setResult(res);
    } catch (err) {
      setResult({
        ok: false,
        error: err instanceof Error ? err.message : "Test failed",
      });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="space-y-3 rounded-md border border-border p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">Proxy</h3>
        <span className="text-xs text-muted-foreground">Optional</span>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="proxy-host" className="text-xs">Host</Label>
          <Input
            id="proxy-host"
            value={value.host}
            onChange={(e) => set("host", e.target.value)}
            placeholder="85.255.176.214"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="proxy-port" className="text-xs">Port</Label>
          <Input
            id="proxy-port"
            type="number"
            value={value.port}
            onChange={(e) => set("port", e.target.value)}
            placeholder="12323"
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="proxy-username" className="text-xs">Username</Label>
        <Input
          id="proxy-username"
          value={value.username}
          onChange={(e) => set("username", e.target.value)}
          placeholder="proxy username"
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="proxy-password" className="text-xs">Password</Label>
        {hasStoredPassword && !passwordEditing ? (
          <div className="flex gap-2">
            <Input
              id="proxy-password"
              value="••••••••••"
              readOnly
              disabled
              type="password"
            />
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => {
                onPasswordEditingChange(true);
                set("password", "");
              }}
            >
              Change
            </Button>
          </div>
        ) : (
          <div className="flex gap-2">
            <Input
              id="proxy-password"
              type="password"
              value={value.password}
              onChange={(e) => set("password", e.target.value)}
              placeholder={hasStoredPassword ? "New password (leave blank to clear)" : "proxy password"}
              autoComplete="new-password"
            />
            {hasStoredPassword && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => {
                  onPasswordEditingChange(false);
                  set("password", "");
                }}
              >
                Keep
              </Button>
            )}
          </div>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="proxy-country" className="text-xs">Country</Label>
        <Input
          id="proxy-country"
          value={value.country}
          onChange={(e) => set("country", e.target.value)}
          placeholder="us, ca, de, gb-london, …"
        />
        <p className="text-xs text-muted-foreground">
          2-letter code, optional <code>-city</code> suffix. Controls user-agent selection
          (Mac for us/ca/gb/au/nz/ie, Windows otherwise).
        </p>
      </div>

      <div className="flex items-center gap-3 pt-1">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={handleTest}
          disabled={testing}
        >
          {testing ? "Testing…" : "Test proxy"}
        </Button>
        {result && (
          <div className="flex-1 min-w-0">
            {result.ok ? (
              <p className="text-xs text-success truncate">
                ✓ Connected — IP {result.ip}
                {result.country ? ` (${result.country})` : ""}
                {result.latency_ms ? ` · ${result.latency_ms}ms` : ""}
              </p>
            ) : (
              <p className="text-xs text-danger truncate">
                ✗ {result.error || "Failed"}
              </p>
            )}
          </div>
        )}
      </div>

      <p className="text-xs text-muted-foreground">
        ISP proxies are purchased per account. Paste the credentials from your provider.
      </p>
    </div>
  );
}

/**
 * Build the payload fields for createAccount / updateAccount from a form value.
 * Handles the "leave password unchanged" contract on edits.
 */
export function proxyFormToCreatePayload(v: ProxyFormValue) {
  const host = v.host.trim();
  const port = v.port ? Number(v.port) : null;
  if (!host || !port) {
    return {
      proxy_host: null,
      proxy_port: null,
      proxy_username: null,
      proxy_password: null,
      proxy_country: v.country.trim() || null,
    };
  }
  return {
    proxy_host: host,
    proxy_port: port,
    proxy_username: v.username.trim() || null,
    proxy_password: v.password || null,
    proxy_country: v.country.trim() || null,
  };
}
