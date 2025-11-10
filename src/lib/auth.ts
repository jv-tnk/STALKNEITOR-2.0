import type { User } from "@supabase/supabase-js";
import { eq } from "drizzle-orm";

import { getDb, users } from "@/lib/db";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export async function getCurrentUser() {
  const supabase = await createSupabaseServerClient();
  const {
    data: { user },
    error,
  } = await supabase.auth.getUser();

  if (error) {
    if (error.message !== "Auth session missing!") {
      console.error("Supabase auth error", error.message);
    }
    return null;
  }

  return user;
}

export async function requireUser() {
  const user = await getCurrentUser();

  if (!user) {
    throw new Error("UNAUTHORIZED");
  }

  return user;
}

export async function syncUserProfile(user: User) {
  const email = user.email ?? "";
  const displayName =
    (user.user_metadata as Record<string, string | undefined>)?.full_name ??
    user.user_metadata?.name ??
    email;

  const db = getDb();

  await db
    .insert(users)
    .values({
      id: user.id,
      email,
      name: displayName,
    })
    .onConflictDoUpdate({
      target: users.id,
      set: {
        email,
        name: displayName,
      },
    });

  await ensureHandle(user.id, email);
}

async function ensureHandle(userId: string, email: string) {
  const db = getDb();
  const existing = await db.query.users.findFirst({
    where: eq(users.id, userId),
  });
  if (existing?.handle) return existing.handle;

  const base = (email.split("@")[0] || "user")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "")
    .slice(0, 20) || "user";

  let candidate = base;
  for (let suffix = 1; ; suffix += 1) {
    const conflict = await db.query.users.findFirst({
      where: eq(users.handle, candidate),
    });
    if (!conflict) {
      await db
        .update(users)
        .set({ handle: candidate })
        .where(eq(users.id, userId));
      return candidate;
    }
    candidate = `${base}${suffix}`;
  }
}
