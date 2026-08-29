"use client";

/**
 * Client-side session state.
 *
 * The JWT lives in `localStorage`: simple, and appropriate for a portfolio
 * demo. A production deployment should move it to an httpOnly, SameSite cookie
 * so it cannot be read by injected scripts - noted in the README.
 */

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import * as api from "./api";
import type { User } from "./types";

const TOKEN_KEY = "documind.token";

interface AuthState {
  user: User | null;
  token: string | null;
  /** True until the stored token has been validated against the API. */
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string) => Promise<void>;
  signOut: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Restore a session on first paint, discarding a token the API rejects.
  useEffect(() => {
    const stored = window.localStorage.getItem(TOKEN_KEY);
    if (!stored) {
      setLoading(false);
      return;
    }
    api
      .fetchMe(stored)
      .then((me) => {
        setUser(me);
        setToken(stored);
      })
      .catch(() => window.localStorage.removeItem(TOKEN_KEY))
      .finally(() => setLoading(false));
  }, []);

  const persist = useCallback((accessToken: string, nextUser: User) => {
    window.localStorage.setItem(TOKEN_KEY, accessToken);
    setToken(accessToken);
    setUser(nextUser);
  }, []);

  const signIn = useCallback(
    async (email: string, password: string) => {
      const result = await api.login(email, password);
      persist(result.access_token, result.user);
      router.push("/chat");
    },
    [persist, router],
  );

  const signUp = useCallback(
    async (email: string, password: string) => {
      const result = await api.signup(email, password);
      persist(result.access_token, result.user);
      router.push("/chat");
    },
    [persist, router],
  );

  const signOut = useCallback(() => {
    window.localStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setUser(null);
    router.push("/login");
  }, [router]);

  const value = useMemo<AuthState>(
    () => ({ user, token, loading, signIn, signUp, signOut }),
    [user, token, loading, signIn, signUp, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside an <AuthProvider>");
  return context;
}
