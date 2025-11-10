import { cookies } from "next/headers";
import { createServerClient } from "@supabase/ssr";

import { serverEnv } from "@/lib/env";

export async function createSupabaseServerClient() {
  const supabaseUrl = serverEnv.SUPABASE_URL ?? process.env.SUPABASE_URL;
  const supabaseAnon = serverEnv.SUPABASE_ANON_KEY ?? process.env.SUPABASE_ANON_KEY;

  if (!supabaseUrl || !supabaseAnon) {
    throw new Error("Supabase environment variables are missing.");
  }

  const store = await cookies();

  return createServerClient(supabaseUrl, supabaseAnon, {
    cookies: {
      get(name) {
        const cookie = store.get(name);
        if (!cookie) return undefined;
        if (typeof cookie === "string") return cookie;
        return "value" in cookie ? cookie.value : undefined;
      },
      set() {
        // no-op; server components cannot mutate cookies
      },
      remove() {
        // no-op; server components cannot mutate cookies
      },
    },
  });
}
