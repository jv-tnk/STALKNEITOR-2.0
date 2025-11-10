import type { ReactNode } from "react";
import { eq } from "drizzle-orm";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/layout/app-shell";
import { getDb, users } from "@/lib/db";
import { getCurrentUser, syncUserProfile } from "@/lib/auth";

export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

export default async function AppLayout({
  children,
}: {
  children: ReactNode;
}) {
  const supabaseUser = await getCurrentUser();

  if (!supabaseUser) {
    redirect("/login");
  }

  const db = getDb();
  let profile = await db.query.users.findFirst({
    where: eq(users.id, supabaseUser.id),
  });

  if (!profile) {
    await syncUserProfile(supabaseUser);
    profile = await db.query.users.findFirst({
      where: eq(users.id, supabaseUser.id),
    });
  }

  const displayName =
    profile?.name ??
    (supabaseUser.user_metadata as Record<string, string | undefined>)?.full_name ??
    supabaseUser.email ??
    "Usuário";

  return (
    <AppShell
      user={{
        id: supabaseUser.id,
        email: supabaseUser.email ?? "",
        name: displayName,
        handle: profile?.handle,
      }}
    >
      {children}
    </AppShell>
  );
}
