"use client";

import { useTheme } from "next-themes";
import { Sun, Moon } from "lucide-react";
import { useSyncExternalStore } from "react";

const subscribeToHydration = () => () => {};

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const mounted = useSyncExternalStore(subscribeToHydration, () => true, () => false);

  // Avoid hydration mismatch — render placeholder until mounted
  if (!mounted) return <div className="p-2 w-9 h-9" />;

  return (
    <button
      className="p-2 rounded-lg hover:bg-graphite transition-colors"
      onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
      aria-label="Toggle theme"
    >
      {theme === "dark" ? (
        <Sun className="h-5 w-5 text-muted-foreground" />
      ) : (
        <Moon className="h-5 w-5 text-muted-foreground" />
      )}
    </button>
  );
}
