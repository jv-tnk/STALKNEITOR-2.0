import { createBrowserClient } from "@supabase/ssr";

import { clientEnv } from "@/lib/env";

export const createSupabaseBrowserClient = () => {
  const supabaseUrl = clientEnv.NEXT_PUBLIC_SUPABASE_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL;
  const supabaseAnon = clientEnv.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!supabaseUrl || !supabaseAnon) {
    throw new Error("NEXT_PUBLIC_SUPABASE_URL/NEXT_PUBLIC_SUPABASE_ANON_KEY are not configured.");
  }

  return createBrowserClient(supabaseUrl, supabaseAnon);
};
