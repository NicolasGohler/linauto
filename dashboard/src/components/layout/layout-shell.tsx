"use client";

import { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { useSidebar } from "./sidebar-context";

export function LayoutShell({ children }: { children: ReactNode }) {
  const { isOpen } = useSidebar();

  return (
    <main
      className={cn(
        "min-h-screen px-4 pb-10 pt-16 md:px-8 md:pt-8 transition-[margin] duration-200",
        // Mobile: no left margin (sidebar is an overlay)
        "ml-0",
        // Desktop: shift right based on sidebar state
        isOpen ? "md:ml-[15.5rem]" : "md:ml-20"
      )}
    >
      {children}
    </main>
  );
}
