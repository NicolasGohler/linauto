"use client";

import { QueryClient } from "@tanstack/react-query";
import { PersistQueryClientProvider, type Persister } from "@tanstack/react-query-persist-client";
import { createSyncStoragePersister } from "@tanstack/query-sync-storage-persister";
import { useState } from "react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QUERY_CACHE_KEY } from "@/lib/query-cache";

// Only small, list-style queries are saved to localStorage so a fresh tab can
// paint them before the network answers. Lead records stay in memory only.
const PERSISTED_ROOTS = new Set(["campaigns", "accounts", "lead-lists", "users", "broadcasts"]);
const DAY_MS = 24 * 60 * 60 * 1000;

const noopPersister: Persister = {
  persistClient: async () => {},
  restoreClient: async () => undefined,
  removeClient: async () => {},
};

function createPersister(): Persister {
  if (typeof window === "undefined") return noopPersister;
  try {
    // Throws in some private modes; fall back to no persistence.
    window.localStorage.getItem(QUERY_CACHE_KEY);
    return createSyncStoragePersister({ storage: window.localStorage, key: QUERY_CACHE_KEY, throttleTime: 1000 });
  } catch {
    return noopPersister;
  }
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 30_000, refetchOnWindowFocus: false, gcTime: DAY_MS },
        },
      })
  );
  const [persister] = useState(createPersister);

  return (
    <PersistQueryClientProvider
      client={queryClient}
      persistOptions={{
        persister,
        maxAge: DAY_MS,
        buster: "v1",
        dehydrateOptions: {
          shouldDehydrateQuery: (q) =>
            q.state.status === "success" && PERSISTED_ROOTS.has(String(q.queryKey[0])),
        },
      }}
    >
      <TooltipProvider>{children}</TooltipProvider>
    </PersistQueryClientProvider>
  );
}
