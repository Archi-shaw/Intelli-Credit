import "./globals.css";

import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import type { Metadata } from "next";

import { Providers } from "@/components/providers";

export const metadata: Metadata = {
  title: { default: "Intelli-Credit", template: "%s · Intelli-Credit" },
  description: "AI-assisted credit assessment and financial due diligence for Indian MSMEs.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-IN" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body className="font-sans"><Providers>{children}</Providers></body>
    </html>
  );
}
