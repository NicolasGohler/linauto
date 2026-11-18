"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import { useAccounts } from "@/hooks/use-queries";
import { getAvatarUrl } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { AddCard } from "@/components/add-card";

type SortOption = "name" | "status" | "pending_desc" | "daily_limit";

const SORT_LABELS: Record<SortOption, string> = {
  name: "Name A→Z",
  status: "Status",
  pending_desc: "Pending requests ↓",
  daily_limit: "Daily limit ↓",
};

const STATUS_ORDER: Record<string, number> = {
  cookie_expired: 0,
  active: 1,
  paused: 2,
  inactive: 3,
};

const PROXY_LABELS: Record<string, string> = {
  us: "US", "us-newyork": "US / New York", "us-losangeles": "US / LA",
  "us-chicago": "US / Chicago", "us-miami": "US / Miami",
  "us-sanfrancisco": "US / SF", "us-dallas": "US / Dallas",
  ca: "Canada", "ca-toronto": "CA / Toronto", "ca-montreal": "CA / Montreal",
  "ca-vancouver": "CA / Vancouver", mx: "Mexico", "mx-mexicocity": "MX / Mexico City",
  gb: "UK", "gb-london": "UK / London",
  de: "Germany", "de-berlin": "DE / Berlin", "de-munich": "DE / Munich", "de-frankfurt": "DE / Frankfurt",
  fr: "France", "fr-paris": "FR / Paris",
  nl: "Netherlands", "nl-amsterdam": "NL / Amsterdam",
  es: "Spain", "es-madrid": "ES / Madrid", "es-barcelona": "ES / Barcelona",
  it: "Italy", "it-rome": "IT / Rome", "it-milan": "IT / Milan",
  ch: "Switzerland", "ch-zurich": "CH / Zurich",
  at: "Austria", "at-vienna": "AT / Vienna",
  pt: "Portugal", "pt-lisbon": "PT / Lisbon",
  gr: "Greece", "gr-athens": "GR / Athens",
  se: "Sweden", ie: "Ireland", pl: "Poland",
  sg: "Singapore", jp: "Japan",
  ae: "UAE", "ae-dubai": "UAE / Dubai", il: "Israel",
  "in": "India", "in-mumbai": "IN / Mumbai",
  br: "Brazil", "br-saopaulo": "BR / São Paulo", ar: "Argentina", co: "Colombia",
  za: "South Africa", ma: "Morocco", ng: "Nigeria",
  au: "Australia", "au-sydney": "AU / Sydney", nz: "New Zealand",
};

function proxyLabel(code: string | null): string {
  if (!code) return "None";
  return PROXY_LABELS[code] || code.replace("-", " / ");
}

function AccountAvatar({ accountId, name }: { accountId: string; name: string }) {
  const initials = name.split(/\s+/).map((w) => w[0]).join("").toUpperCase().slice(0, 2);
  return (
    <div className="relative h-10 w-10 shrink-0 overflow-hidden rounded-full bg-muted">
      <img
        src={getAvatarUrl(accountId)} alt=""
        className="h-full w-full object-cover"
        onError={(e) => {
          (e.currentTarget as HTMLImageElement).style.display = "none";
          (e.currentTarget.nextElementSibling as HTMLElement).style.display = "flex";
        }}
      />
      <span
        className="absolute inset-0 hidden items-center justify-center text-xs font-bold text-muted-foreground"
        style={{ display: "none" }}
        ref={(el) => { if (el) el.style.display = "flex"; }}
      >
        {initials}
      </span>
    </div>
  );
}

export default function AccountsPage() {
  const { data: accounts, isLoading } = useAccounts();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [sortBy, setSortBy] = useState<SortOption>("status");

  const filtered = useMemo(() => {
    if (!accounts) return [];
    let list = accounts;
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter((a) => a.name?.toLowerCase().includes(q));
    }
    if (statusFilter) {
      list = list.filter((a) => a.status === statusFilter);
    }
    return [...list].sort((a, b) => {
      if (sortBy === "name") return (a.name ?? "").localeCompare(b.name ?? "");
      if (sortBy === "status") {
        const sa = STATUS_ORDER[a.status] ?? 99;
        const sb = STATUS_ORDER[b.status] ?? 99;
        return sa !== sb ? sa - sb : (a.name ?? "").localeCompare(b.name ?? "");
      }
      if (sortBy === "pending_desc") return (b.pending_requests ?? 0) - (a.pending_requests ?? 0);
      if (sortBy === "daily_limit") return (b.daily_limit ?? 0) - (a.daily_limit ?? 0);
      return 0;
    });
  }, [accounts, search, statusFilter, sortBy]);

  const uniqueStatuses = useMemo(() => {
    if (!accounts) return [];
    return Array.from(new Set(accounts.map((a) => a.status))).sort();
  }, [accounts]);

  return (
    <div className="space-y-6">
      <PageHeader title="Accounts" description="Manage your LinkedIn accounts">
        <Link href="/accounts/new">
          <Button>Add Account</Button>
        </Link>
      </PageHeader>

      <div className="flex flex-wrap items-center gap-3">
        <Input
          placeholder="Search accounts…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />
        <select
          className="h-9 rounded-md border border-border bg-background px-2 text-sm"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">All statuses</option>
          {uniqueStatuses.map((s) => (
            <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
          ))}
        </select>
        <select
          className="h-9 rounded-md border border-border bg-background px-2 text-sm"
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value as SortOption)}
        >
          {(Object.keys(SORT_LABELS) as SortOption[]).map((k) => (
            <option key={k} value={k}>{SORT_LABELS[k]}</option>
          ))}
        </select>
      </div>

      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 2 }).map((_, i) => <Skeleton key={i} className="h-36" />)}
        </div>
      ) : filtered.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12">
            <p className="text-muted-foreground">{accounts?.length === 0 ? "No accounts yet" : "No accounts match your search"}</p>
            {accounts?.length === 0 && (
              <Link href="/accounts/new">
                <Button variant="outline" className="mt-4">Add your first account</Button>
              </Link>
            )}
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {filtered.map((a) => (
            <Link key={a.id} href={`/accounts/${a.id}`}>
              <Card className="hover:border-muted-foreground/30 transition-colors cursor-pointer">
                <CardContent className="p-5 space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <AccountAvatar accountId={a.id} name={a.name} />
                      <h3 className="font-medium">{a.name}</h3>
                    </div>
                    <StatusBadge status={a.status} />
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    <div>
                      <p className="text-xs text-muted-foreground">Daily Target</p>
                      <p>{a.daily_limit}/day</p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Pending Requests</p>
                      <p className={a.pending_requests != null && a.pending_requests > 1000 ? "text-warning" : ""}>
                        {a.pending_invitations_count != null ? a.pending_invitations_count : (a.pending_requests ?? "—")}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Timezone</p>
                      <p>{a.timezone ?? "—"}</p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Proxy</p>
                      <p>{proxyLabel(a.proxy_country)}</p>
                    </div>
                  </div>
                  {a.paused_until && (
                    <p className="text-xs text-warning">
                      Paused until {new Date(a.paused_until.endsWith("Z") ? a.paused_until : a.paused_until + "Z").toLocaleString(undefined, { timeZone: a.timezone ?? undefined })}
                    </p>
                  )}
                </CardContent>
              </Card>
            </Link>
          ))}
          <AddCard href="/accounts/new" label="Add account" />
        </div>
      )}
    </div>
  );
}
