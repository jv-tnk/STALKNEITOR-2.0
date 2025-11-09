import { z } from "zod";

const serverSchema = z
  .object({
    SUPABASE_URL: z.string().url(),
    SUPABASE_ANON_KEY: z.string(),
    SUPABASE_SERVICE_ROLE: z.string(),
    DATABASE_URL: z.string(),
    GITHUB_TOKEN: z.string(),
    ETL_SECRET: z.string(),
    NEXT_PUBLIC_APP_URL: z.string().url(),
    SENTRY_DSN: z.string().optional(),
    PLAUSIBLE_DOMAIN: z.string().optional(),
  })
  .partial({ SENTRY_DSN: true, PLAUSIBLE_DOMAIN: true });

const clientSchema = z.object({
  NEXT_PUBLIC_SUPABASE_URL: z.string().url(),
  NEXT_PUBLIC_SUPABASE_ANON_KEY: z.string(),
  NEXT_PUBLIC_APP_URL: z.string().url(),
});

export type ServerEnv = z.infer<typeof serverSchema>;
export type ClientEnv = z.infer<typeof clientSchema>;

function parseEnv<T extends z.ZodTypeAny>(schema: T) {
  const result = schema.safeParse(process.env);

  if (!result.success) {
    const formatted = result.error.flatten().fieldErrors;
    if (process.env.NODE_ENV === "production") {
      throw new Error(
        `Missing or invalid environment variables: ${Object.keys(formatted).join(", ")}`,
      );
    }
    console.warn(
      "Environment validation failed. Falling back to partial values:",
      formatted,
    );
    return {} as z.infer<T>;
  }

  return result.data;
}

export const serverEnv = parseEnv(serverSchema);
export const clientEnv = parseEnv(clientSchema);
