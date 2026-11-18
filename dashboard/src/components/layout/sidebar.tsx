"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { Target, List, Users, UserCircle, Archive, Globe, Activity, Menu, X, Mail, type LucideIcon } from "lucide-react";
import { useSidebar } from "./sidebar-context";
import { BrandMark, Wordmark } from "./brand-mark";

const navItems: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/campaigns", label: "Campaigns", icon: Target },
  { href: "/broadcasts", label: "Broadcasts", icon: Mail },
  { href: "/lead-lists", label: "Lists", icon: List },
  { href: "/leads", label: "Leads", icon: Users },
  { href: "/accounts", label: "Accounts", icon: UserCircle },
  { href: "/activity", label: "Activity", icon: Activity },
  { href: "/scrapers", label: "Scrapers", icon: Globe },
  { href: "/archive", label: "Archive", icon: Archive },
];

export function Sidebar() {
  const pathname = usePathname();
  const { isOpen, toggle, close } = useSidebar();

  return (
    <>
      {/* Mobile backdrop */}
      {isOpen && (
        <div
          className="fixed inset-0 z-30 bg-[#1c1911]/40 backdrop-blur-[2px] md:hidden"
          onClick={close}
          aria-hidden="true"
        />
      )}

      <aside
        className={cn(
          "fixed left-3 top-3 bottom-3 z-40 flex flex-col overflow-hidden rounded-[18px] bg-forest-stage text-sidebar-foreground shadow-float transition-[transform,width] duration-200",
          // Desktop: always in flow; width switches between full and icon-only
          "md:translate-x-0",
          isOpen ? "w-56" : "md:w-14",
          // Mobile: slide in/out
          isOpen ? "translate-x-0" : "-translate-x-[calc(100%+1rem)] md:translate-x-0"
        )}
      >
        {/* Header */}
        <div className={cn("flex h-16 items-center px-3", !isOpen && "md:h-auto md:flex-col md:gap-2 md:px-2 md:py-4")}>
          {/* Logo — hidden when desktop-collapsed */}
          <Link
            href="/campaigns"
            className={cn(
              "flex items-center gap-2.5 overflow-hidden transition-all",
              !isOpen && "md:hidden"
            )}
            onClick={() => {
              // Close drawer on mobile when navigating
              if (window.innerWidth < 768) close();
            }}
          >
            <Wordmark className="pl-1 text-[22px] text-sidebar-foreground whitespace-nowrap" markClassName="h-6 w-6" />
          </Link>

          {/* Logo icon only when desktop-collapsed */}
          <Link
            href="/campaigns"
            className={cn(
              "hidden items-center justify-center",
              !isOpen && "md:flex"
            )}
          >
            <BrandMark className="h-6 w-6 text-sidebar-foreground" />
          </Link>

          {/* Toggle button — inside sidebar (desktop) or close button (mobile) */}
          <button
            onClick={toggle}
            className={cn("ml-auto flex h-8 w-8 shrink-0 items-center justify-center rounded-[7px] text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground transition-colors", !isOpen && "md:ml-0")}
            aria-label={isOpen ? "Collapse sidebar" : "Expand sidebar"}
          >
            {isOpen ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
          </button>
        </div>

        {/* Nav */}
        <nav className="flex flex-col gap-0.5 px-2 pt-2">
          {navItems.map((item) => {
            const active = pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => {
                  if (window.innerWidth < 768) close();
                }}
                className={cn(
                  "flex items-center gap-3 rounded-[9px] px-3 py-2 text-sm font-medium transition-colors",
                  active
                    ? "bg-sidebar-primary text-sidebar-primary-foreground shadow-soft"
                    : "text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                  // When desktop-collapsed: center the icon
                  !isOpen && "md:justify-center md:px-2"
                )}
                title={!isOpen ? item.label : undefined}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className={cn(!isOpen && "md:hidden")}>{item.label}</span>
              </Link>
            );
          })}
        </nav>
      </aside>
    </>
  );
}

/** Hamburger button rendered in the main content area on mobile (when sidebar is closed). */
export function SidebarToggle() {
  const { isOpen, toggle } = useSidebar();
  if (isOpen) return null;
  return (
    <button
      onClick={toggle}
      className="fixed left-3 top-3 z-50 flex h-9 w-9 items-center justify-center rounded-[9px] bg-sidebar text-sidebar-foreground/80 shadow-float hover:bg-sidebar-accent hover:text-sidebar-accent-foreground transition-colors md:hidden"
      aria-label="Open sidebar"
    >
      <Menu className="h-4 w-4" />
    </button>
  );
}
