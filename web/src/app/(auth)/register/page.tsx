"use client";

import { useState, FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { MurmurLogoMark } from "@/components/murmur-doodles";
import { Input } from "@/components/ui/input";
import { setToken } from "@/hooks/use-auth";
import { API_BASE } from "@/lib/api";

export default function RegisterPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const res = await fetch(`${API_BASE}/api/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, email, password }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || data.error || "Registration failed");
      }

      const data = await res.json();
      if (data.token) {
        setToken(data.token);
      }

      router.push("/dashboard");
    } catch (err: any) {
      setError(err.message || "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-background flex items-center justify-center px-6">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="flex items-center justify-center gap-3 mb-10">
          <MurmurLogoMark className="h-8 w-8" />
          <span className="text-xl font-semibold tracking-tight">Murmur</span>
        </div>

        {/* Card */}
        <div className="glass-card rounded-2xl p-8">
          <h1 className="text-2xl font-bold tracking-tight mb-1">Create your account</h1>
          <p className="text-sm text-chalk-soft mb-8">
            Set up your profile and start learning with a personalized AI tutor.
          </p>

          <form onSubmit={handleSubmit} className="space-y-6">
            <div>
              <label htmlFor="name" className="block text-xs font-mono text-chalk-soft mb-2 uppercase tracking-wider">
                Name
              </label>
              <Input
                id="name"
                type="text"
                placeholder="Your name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                autoComplete="name"
              />
            </div>

            <div>
              <label htmlFor="email" className="block text-xs font-mono text-chalk-soft mb-2 uppercase tracking-wider">
                Email
              </label>
              <Input
                id="email"
                type="email"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
              />
            </div>

            <div>
              <label htmlFor="password" className="block text-xs font-mono text-chalk-soft mb-2 uppercase tracking-wider">
                Password
              </label>
              <Input
                id="password"
                type="password"
                placeholder="At least 8 characters"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={8}
                autoComplete="new-password"
              />
            </div>

            {error && (
              <p className="text-sm text-ember">{error}</p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full h-12 bg-amber text-void font-semibold rounded-xl hover:brightness-110 transition-all active:scale-[0.97] disabled:opacity-50 disabled:pointer-events-none flex items-center justify-center gap-2"
            >
              {loading && <Loader2 className="h-4 w-4 animate-spin" />}
              Create account
            </button>
          </form>
        </div>

        {/* Footer link */}
        <p className="text-center text-sm text-chalk-soft mt-6">
          Already have an account?{" "}
          <Link href="/login" className="text-amber hover:underline font-medium">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
