import "./globals.css";
import type { Metadata, Viewport } from "next";

export const metadata: Metadata = { title: "NEW SAPIENS — Your Workday. Understood. Documented. Optimized.", description: "AI work intelligence platform" };
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function Root({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="font-sans">{children}</body>
    </html>
  );
}
