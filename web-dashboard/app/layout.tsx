import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Receipt Review | Reconciliation Command Center",
  description: "Evidence-backed receipt reconciliation with Mistral OCR, Docling, policy controls, and Langfuse traces.",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
  openGraph: {
    title: "Receipt Review",
    description: "Evidence. Policy. Decision.",
    images: ["/receipt-review-og.png"],
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
