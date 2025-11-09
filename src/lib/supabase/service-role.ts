import { createClient } from "@supabase/supabase-js";

import { serverEnv } from "@/lib/env";

let adminClient: ReturnType<typeof createClient> | undefined;

export function getSupabaseServiceRoleClient() {
  if (adminClient) return adminClient;

  const supabaseUrl = serverEnv.SUPABASE_URL ?? process.env.SUPABASE_URL;
  const serviceRoleKey = serverEnv.SUPABASE_SERVICE_ROLE ?? process.env.SUPABASE_SERVICE_ROLE;

  if (!supabaseUrl || !serviceRoleKey) {
    throw new Error("SUPABASE_URL or SUPABASE_SERVICE_ROLE are missing.");
  }

  adminClient = createClient(supabaseUrl, serviceRoleKey, {
    auth: {
      persistSession: false,
    },
  });

  return adminClient;
}
