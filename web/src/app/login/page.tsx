"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { GlassmorphicCard } from "@/components/ui/glassmorphic-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { signIn, signUp, signInWithGoogle } from "@/hooks/use-auth";

export default function LoginPage() {
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      if (mode === "signin") {
        await signIn(email, password);
      } else {
        await signUp(email, password);
      }
      router.push("/dashboard");
    } catch (err: any) {
      const code = err?.code || "";
      if (code === "auth/user-not-found" || code === "auth/wrong-password" || code === "auth/invalid-credential") {
        setError("Invalid email or password.");
      } else if (code === "auth/email-already-in-use") {
        setError("An account with this email already exists.");
      } else if (code === "auth/weak-password") {
        setError("Password should be at least 6 characters.");
      } else if (code === "auth/invalid-email") {
        setError("Please enter a valid email address.");
      } else {
        setError(err?.message || "Something went wrong.");
      }
    } finally {
      setLoading(false);
    }
  };

  const handleGoogle = async () => {
    setError("");
    try {
      await signInWithGoogle();
      router.push("/dashboard");
    } catch (err: any) {
      if (err?.code !== "auth/popup-closed-by-user") {
        setError(err?.message || "Google sign-in failed.");
      }
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        {/* Logo / Title */}
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold tracking-tight text-foreground">
            Murmur
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            {mode === "signin" ? "Welcome back" : "Create your account"}
          </p>
        </div>

        <GlassmorphicCard padding="xl">
          <form onSubmit={handleSubmit} className="space-y-5">
            <div className="space-y-4">
              <div>
                <label
                  htmlFor="email"
                  className="block text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2"
                >
                  Email
                </label>
                <Input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  required
                  autoComplete="email"
                />
              </div>
              <div>
                <label
                  htmlFor="password"
                  className="block text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2"
                >
                  Password
                </label>
                <Input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={mode === "signup" ? "At least 6 characters" : ""}
                  required
                  minLength={6}
                  autoComplete={mode === "signin" ? "current-password" : "new-password"}
                />
              </div>
            </div>

            {error && (
              <p className="text-sm text-ember">{error}</p>
            )}

            <Button
              type="submit"
              className="w-full"
              disabled={loading}
            >
              {loading
                ? "..."
                : mode === "signin"
                ? "Sign in"
                : "Create account"}
            </Button>
          </form>

          {/* Divider */}
          <div className="flex items-center gap-3 my-5">
            <div className="flex-1 h-px bg-chalk-faint" />
            <span className="text-xs text-muted-foreground">or</span>
            <div className="flex-1 h-px bg-chalk-faint" />
          </div>

          {/* Google */}
          <Button
            variant="outline"
            className="w-full"
            onClick={handleGoogle}
            type="button"
          >
            <svg className="h-4 w-4 mr-2" viewBox="0 0 24 24">
              <path
                fill="currentColor"
                d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"
              />
              <path
                fill="currentColor"
                d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
              />
              <path
                fill="currentColor"
                d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
              />
              <path
                fill="currentColor"
                d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
              />
            </svg>
            Continue with Google
          </Button>

          {/* Toggle mode */}
          <p className="text-center text-sm text-muted-foreground mt-5">
            {mode === "signin" ? (
              <>
                Don&apos;t have an account?{" "}
                <button
                  type="button"
                  onClick={() => { setMode("signup"); setError(""); }}
                  className="text-amber hover:underline font-medium"
                >
                  Sign up
                </button>
              </>
            ) : (
              <>
                Already have an account?{" "}
                <button
                  type="button"
                  onClick={() => { setMode("signin"); setError(""); }}
                  className="text-amber hover:underline font-medium"
                >
                  Sign in
                </button>
              </>
            )}
          </p>
        </GlassmorphicCard>
      </div>
    </div>
  );
}
