import { cookies } from "next/headers";
import { createServerClient } from "@supabase/ssr";

import { serverEnv } from "@/lib/env";

export function createSupabaseServerClient() {
  const cookieStore = cookies();
  const supabaseUrl = serverEnv.SUPABASE_URL ?? process.env.SUPABASE_URL;
  const supabaseAnon = serverEnv.SUPABASE_ANON_KEY ?? process.env.SUPABASE_ANON_KEY;

  if (!supabaseUrl || !supabaseAnon) {
    throw new Error("Supabase environment variables are missing.");
  }

  return createServerClient(supabaseUrl, supabaseAnon, {
    cookies: {
      get(name) {
        return cookieStore.get(name)?.value;
      },
      set(name, value, options) {
        cookieStore.set({
          name,
          value,
          ...options,
        });
      },
      remove(name, options) {
        cookieStore.delete({ name, ...options });
      },
    },
  });
}
