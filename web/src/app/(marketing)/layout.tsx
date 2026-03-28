import Link from "next/link";
import { MurmurLogoMark } from "@/components/murmur-doodles";

export default function MarketingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-background">
      {/* Marketing Nav */}
      <nav className="fixed top-0 left-0 right-0 z-50 glass-card border-b border-chalk-faint/30">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-3 group">
            <MurmurLogoMark />
            <span className="text-lg font-semibold tracking-tight group-hover:text-amber transition-colors">
              Murmur
            </span>
          </Link>

          <div className="flex items-center gap-4">
            <Link
              href="/login"
              className="text-sm font-medium text-chalk-soft hover:text-foreground transition-colors px-4 py-2"
            >
              Sign In
            </Link>
            <Link
              href="/register"
              className="text-sm font-medium bg-amber text-void px-5 py-2.5 rounded-xl hover:brightness-110 transition-all active:scale-[0.97]"
            >
              Get Started
            </Link>
          </div>
        </div>
      </nav>

      {children}
    </div>
  );
}
