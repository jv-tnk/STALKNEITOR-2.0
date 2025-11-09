import { drizzle, type NodePgDatabase } from "drizzle-orm/node-postgres";
import { Pool } from "pg";

import { serverEnv } from "@/lib/env";
import * as schema from "@/lib/db/schema";

const globalForDb = globalThis as unknown as {
  __dbPool?: Pool;
  __dbInstance?: NodePgDatabase<typeof schema>;
};

function resolveConnectionString() {
  return serverEnv.DATABASE_URL ?? process.env.DATABASE_URL;
}

function createPool() {
  const connectionString = resolveConnectionString();

  if (!connectionString) {
    throw new Error(
      "DATABASE_URL is not set. Make sure to add it to your environment.",
    );
  }

  return new Pool({
    connectionString,
    max: 5,
    ssl: connectionString.includes("localhost")
      ? false
      : { rejectUnauthorized: false },
  });
}

export function getDb() {
  if (globalForDb.__dbInstance) {
    return globalForDb.__dbInstance;
  }

  const pool = globalForDb.__dbPool ?? createPool();
  if (process.env.NODE_ENV !== "production") {
    globalForDb.__dbPool = pool;
  }

  const instance = drizzle(pool, { schema });
  if (process.env.NODE_ENV !== "production") {
    globalForDb.__dbInstance = instance;
  }

  return instance;
}

export type Database = ReturnType<typeof getDb>;
