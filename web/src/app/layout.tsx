import type { Metadata } from "next";
import { Bodoni_Moda, Inter, Geist_Mono, Mr_Dafoe, Cormorant_Garamond } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/toaster";

// Editorial display serif — the award-winning voice (per owner's font inspiration)
const bodoni = Bodoni_Moda({
  variable: "--font-bodoni",
  subsets: ["latin"],
  display: "swap",
});

// Clean geometric sans — UI body (per glassmorphism page inspiration)
const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

// Mono — terminal data only (wire, codes, timestamps)
const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  display: "swap",
});

// Handwritten signature script — brand mark only (per Oakley-style inspiration)
const dafoe = Mr_Dafoe({
  variable: "--font-dafoe",
  subsets: ["latin"],
  weight: "400",
  display: "swap",
});

// Elegant italic serif — accent flourish on hero numerals & quotes
const cormorant = Cormorant_Garamond({
  variable: "--font-cormorant",
  subsets: ["latin"],
  style: ["italic", "normal"],
  weight: ["300", "400", "500"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "GOLD DESK COMMAND — XAUUSD H1 Decision Harness",
  description:
    "Fail-closed, human-in-the-loop XAUUSD H1 decision desk telemetry. Read-only over an append-only journal.",
  keywords: ["XAUUSD", "gold", "trading desk", "decision harness", "journal"],
  icons: {
    icon: "https://z-cdn.chatglm.cn/z-ai/static/logo.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${bodoni.variable} ${inter.variable} ${geistMono.variable} ${dafoe.variable} ${cormorant.variable} antialiased bg-background text-foreground`}
      >
        {children}
        <Toaster />
      </body>
    </html>
  );
}
