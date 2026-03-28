"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  onAuthStateChanged,
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  signInWithPopup,
  GoogleAuthProvider,
  signOut as firebaseSignOut,
  updateProfile,
} from "firebase/auth";
import { auth, getCurrentUser } from "@/lib/firebase";

export interface User {
  id: string;
  email: string;
  name: string | null;
}

export async function signIn(email: string, password: string) {
  return signInWithEmailAndPassword(auth, email, password);
}

export async function signUp(email: string, password: string, name?: string) {
  const cred = await createUserWithEmailAndPassword(auth, email, password);
  if (name?.trim()) {
    await updateProfile(cred.user, { displayName: name.trim() });
  }
  return cred;
}

export async function signInWithGoogle() {
  const provider = new GoogleAuthProvider();
  return signInWithPopup(auth, provider);
}

export async function getIdToken(): Promise<string | null> {
  const user = await getCurrentUser();
  if (!user) return null;
  return user.getIdToken();
}

export function useAuth() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (firebaseUser) => {
      if (firebaseUser) {
        setUser({
          id: firebaseUser.uid,
          email: firebaseUser.email || "",
          name: firebaseUser.displayName,
        });
      } else {
        setUser(null);
      }
      setIsLoading(false);
    });
    return unsubscribe;
  }, []);

  const logout = useCallback(async () => {
    await firebaseSignOut(auth);
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
