"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";

if (typeof window !== "undefined" && !process.env.NEXT_PUBLIC_API_URL) {
  console.warn("[Auth] NEXT_PUBLIC_API_URL is not set — falling back to http://localhost:8000");
}
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface User {
  id: string;
  email: string;
  name: string | null;
}

const TOKEN_KEY = "murmur_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export function useAuth() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setIsLoading(false);
      return;
    }

    fetch(`${API_BASE}/api/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((res) => {
        if (!res.ok) throw new Error("Unauthorized");
        return res.json();
      })
      .then((data: { user: User }) => setUser(data.user))
      .catch(() => {
        clearToken();
        setUser(null);
      })
      .finally(() => setIsLoading(false));
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
    router.push("/login");
  }, [router]);

  return {
    user,
    isLoading,
    isAuthenticated: !!user,
    logout,
  };
}
