import { NextRequest, NextResponse } from "next/server";

import { syncUserProfile } from "@/lib/auth";
import { serverEnv } from "@/lib/env";
import { createServerClient } from "@supabase/ssr";

const supabaseUrl = serverEnv.SUPABASE_URL ?? process.env.SUPABASE_URL;
const supabaseAnon = serverEnv.SUPABASE_ANON_KEY ?? process.env.SUPABASE_ANON_KEY;

if (!supabaseUrl || !supabaseAnon) {
  throw new Error("Supabase environment variables are missing.");
}

export async function GET(request: NextRequest) {
  const url = new URL(request.url);
  const code = url.searchParams.get("code") ?? url.searchParams.get("token");
  const error = url.searchParams.get("error");

  if (error) {
    return NextResponse.redirect(
      new URL(`/login?message=${encodeURIComponent(error)}`, request.url),
    );
  }

  if (!code) {
    return NextResponse.redirect(
      new URL(`/login?message=${encodeURIComponent("Link inválido")}`, request.url),
    );
  }

  const response = NextResponse.redirect(new URL("/dashboard", request.url));

  const supabase = createServerClient(supabaseUrl, supabaseAnon, {
    cookies: {
      get(name) {
        return request.cookies.get(name)?.value;
      },
      set(name, value, options) {
        response.cookies.set({
          name,
          value,
          ...options,
        });
      },
      remove(name, options) {
        response.cookies.delete({ name, ...options });
      },
    },
  });

  const { data, error: exchangeError } = await supabase.auth.exchangeCodeForSession(code);

  if (exchangeError) {
    return NextResponse.redirect(
      new URL(`/login?message=${encodeURIComponent(exchangeError.message)}`, request.url),
    );
  }

  if (data.user) {
    await syncUserProfile(data.user);
  }

  return response;
}
