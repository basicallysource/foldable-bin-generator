import type { Metadata } from "next";
import { IBM_Plex_Mono } from "next/font/google";
import "./globals.css";

const plex = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "600", "700"],
  variable: "--font-plex",
});

export const metadata: Metadata = {
  title: "binflatten · rev02",
  description:
    "Turn a bin's STEP CAD into a foldable laser-cuttable flat pattern (SVG/DXF) for LightBurn.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={plex.variable}>
      <body>{children}</body>
    </html>
  );
}
