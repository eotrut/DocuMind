"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { LogoIcon } from "@/components/Icons";
import { useAuth } from "@/lib/auth";

/** Entry point: send signed-in users to the workspace, everyone else to login. */
export default function HomePage() {
  const router = useRouter();
  const { user, loading } = useAuth();

  useEffect(() => {
    if (loading) return;
    router.replace(user ? "/chat" : "/login");
  }, [loading, user, router]);

  return (
    <main className="flex min-h-screen items-center justify-center">
      <div className="flex flex-col items-center gap-3 text-ink-400">
        <LogoIcon className="h-8 w-8 animate-pulse text-accent-400" />
        <p className="text-sm">Loading DocuMind…</p>
      </div>
    </main>
  );
}
