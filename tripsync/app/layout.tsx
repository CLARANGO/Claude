import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TripSync — Plan trips with friends",
  description: "Coordinate group travel by syncing availability, budget, and departure cities to find the perfect destination.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">
        {children}
      </body>
    </html>
  );
}
