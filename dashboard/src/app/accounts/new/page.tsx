"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useCreateAccount } from "@/hooks/use-queries";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import {
  ProxySettings,
  emptyProxyForm,
  proxyFormToCreatePayload,
  type ProxyFormValue,
} from "@/components/proxy-settings";

type LoginMethod = "cookie" | "browser";

export default function NewAccountPage() {
  const router = useRouter();
  const createAccount = useCreateAccount();

  const [name, setName] = useState("");
  const [cookie, setCookie] = useState("");
  const [timezone, setTimezone] = useState("Europe/Berlin");
  const [proxy, setProxy] = useState<ProxyFormValue>(emptyProxyForm);
  const [method, setMethod] = useState<LoginMethod>("cookie");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name) {
      toast.error("Account name is required");
      return;
    }
    if (method === "cookie" && !cookie) {
      toast.error("li_at cookie is required");
      return;
    }

    const proxyPayload = proxyFormToCreatePayload(proxy);
    createAccount.mutate(
      {
        name,
        li_at_cookie: method === "cookie" ? cookie : undefined,
        timezone,
        proxy_host: proxyPayload.proxy_host ?? undefined,
        proxy_port: proxyPayload.proxy_port ?? undefined,
        proxy_username: proxyPayload.proxy_username ?? undefined,
        proxy_password: proxyPayload.proxy_password ?? undefined,
        proxy_country: proxyPayload.proxy_country ?? undefined,
      },
      {
        onSuccess: (account) => {
          if (method === "browser") {
            toast.success("Account created — use the login browser to authenticate");
          } else {
            toast.success("Account created");
          }
          router.push(`/accounts/${account.id}`);
        },
        onError: (err) => toast.error(err.message),
      }
    );
  }

  return (
    <div className="space-y-6 max-w-xl">
      <PageHeader title="Add Account" />

      <Card>
        <CardHeader>
          <CardTitle>New LinkedIn Account</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="name">Account Name</Label>
              <Input
                id="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., nicolas"
              />
            </div>

            <div className="space-y-2">
              <Label>Login Method</Label>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant={method === "cookie" ? "secondary" : "ghost"}
                  size="sm"
                  onClick={() => setMethod("cookie")}
                >
                  Paste Cookie
                </Button>
                <Button
                  type="button"
                  variant={method === "browser" ? "secondary" : "ghost"}
                  size="sm"
                  onClick={() => setMethod("browser")}
                >
                  Login via Browser
                </Button>
              </div>
            </div>

            {method === "cookie" && (
              <div className="space-y-2">
                <Label htmlFor="cookie">li_at Cookie</Label>
                <Input
                  id="cookie"
                  value={cookie}
                  onChange={(e) => setCookie(e.target.value)}
                  placeholder="Paste your li_at cookie value"
                  type="password"
                />
                <p className="text-xs text-muted-foreground">
                  Find this in your browser DevTools under Application &gt; Cookies &gt; linkedin.com
                </p>
              </div>
            )}

            {method === "browser" && (
              <div className="rounded-md border border-info-line bg-info-soft/70 px-4 py-3">
                <p className="text-sm text-info">
                  After creating the account, you&apos;ll be redirected to the account page where you can open a login browser to authenticate with LinkedIn directly.
                </p>
              </div>
            )}

            <div className="space-y-2">
              <Label htmlFor="timezone">Timezone</Label>
              <select
                id="timezone"
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
              >
                <option value="America/New_York">US — EST (New York)</option>
                <option value="Europe/London">UK — GMT (London)</option>
                <option value="Europe/Berlin">Europe — CET (Berlin)</option>
                <option value="Europe/Rome">Italy — CET (Rome)</option>
                <option value="Europe/Athens">Greece — EET (Athens)</option>
                <option value="Europe/Paris">France — CET (Paris)</option>
                <option value="Europe/Madrid">Spain — CET (Madrid)</option>
                <option value="Asia/Dubai">UAE — GST (Dubai)</option>
                <option value="Asia/Singapore">Asia — SGT (Singapore)</option>
                <option value="America/Toronto">Canada — EST (Toronto)</option>
                <option value="America/Vancouver">Canada — PST (Vancouver)</option>
              </select>
            </div>

            <ProxySettings
              value={proxy}
              onChange={setProxy}
              hasStoredPassword={false}
              passwordEditing={true}
              onPasswordEditingChange={() => {}}
            />

            <Button type="submit" disabled={createAccount.isPending}>
              {createAccount.isPending ? "Creating..." : "Add Account"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
