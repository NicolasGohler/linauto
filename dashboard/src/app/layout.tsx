import type { Metadata } from "next";
import { Geist_Mono } from "next/font/google";
import { Manrope } from "next/font/google";
import "./globals.css";
import { Providers } from "@/components/providers";
import { Sidebar, SidebarToggle } from "@/components/layout/sidebar";
import { SidebarProvider } from "@/components/layout/sidebar-context";
import { Toaster } from "@/components/ui/sonner";
import { LayoutShell } from "@/components/layout/layout-shell";

const manrope = Manrope({
  variable: "--font-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Releasi",
  description: "LinkedIn automation dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${manrope.variable} ${geistMono.variable} antialiased`}
      >
        <Providers>
          <SidebarProvider>
            <Sidebar />
            <SidebarToggle />
            <LayoutShell>{children}</LayoutShell>
          </SidebarProvider>
          <Toaster />
        </Providers>
      </body>
    </html>
  );
}
