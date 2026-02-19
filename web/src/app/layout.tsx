import type { Metadata } from "next";
import { Inter, Fira_Code, Caveat, Patrick_Hand } from "next/font/google";
import "./globals.css";
import "@excalidraw/excalidraw/index.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const firaCode = Fira_Code({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

const caveat = Caveat({
  subsets: ["latin"],
  variable: "--font-handwriting",
  display: "swap",
});

const patrickHand = Patrick_Hand({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-handwriting-alt",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Voice AI",
  description: "Real-time voice assistant with canvas capabilities",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className={`${inter.variable} ${firaCode.variable} ${caveat.variable} ${patrickHand.variable} font-sans antialiased`}>
        {children}
      </body>
    </html>
  );
}

