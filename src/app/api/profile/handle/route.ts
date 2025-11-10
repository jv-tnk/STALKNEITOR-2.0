import { NextRequest } from "next/server";
import { eq } from "drizzle-orm";
import { z } from "zod";

import { errorResponse, successResponse } from "@/app/api/_lib/responses";
import { requireUser } from "@/lib/auth";
import { getDb, users } from "@/lib/db";

const payloadSchema = z.object({
  handle: z
    .string()
    .min(3)
    .max(24)
    .regex(/^[a-zA-Z0-9_]+$/, "Use apenas letras, números ou underscore"),
});

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  const parsed = payloadSchema.safeParse(body);

  if (!parsed.success) {
    return errorResponse(400, "Handle inválido", parsed.error.format());
  }

  const user = await requireUser().catch(() => null);
  if (!user) {
    return errorResponse(401, "Unauthorized");
  }

  const db = getDb();
  const existing = await db.query.users.findFirst({
    where: eq(users.handle, parsed.data.handle.toLowerCase()),
  });

  if (existing && existing.id !== user.id) {
    return errorResponse(409, "Este handle já está em uso");
  }

  const [updated] = await db
    .update(users)
    .set({ handle: parsed.data.handle.toLowerCase() })
    .where(eq(users.id, user.id))
    .returning();

  return successResponse({ handle: updated?.handle ?? parsed.data.handle });
}
