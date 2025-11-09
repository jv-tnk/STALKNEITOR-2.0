import { NextRequest } from "next/server";
import { and, eq } from "drizzle-orm";
import { z } from "zod";

import { errorResponse, successResponse } from "@/app/api/_lib/responses";
import { requireUser } from "@/lib/auth";
import { getDb, problemProgress } from "@/lib/db";
import { PROGRESS_STATUSES } from "@/lib/constants/progress";

const payloadSchema = z.object({
  problemId: z.string().uuid(),
  status: z.enum(PROGRESS_STATUSES),
  attempts: z.number().int().min(0).optional(),
  lastResult: z.string().nullable().optional(),
});

export async function POST(req: NextRequest) {
  const json = await req.json().catch(() => null);
  const parsed = payloadSchema.safeParse(json);

  if (!parsed.success) {
    return errorResponse(400, "Invalid payload", parsed.error.format());
  }

  let userId: string;
  try {
    userId = (await requireUser()).id;
  } catch {
    return errorResponse(401, "Unauthorized");
  }

  const db = getDb();
  const data = parsed.data;

  await db
    .insert(problemProgress)
    .values({
      userId,
      problemId: data.problemId,
      status: data.status,
      attempts: data.attempts ?? 0,
      lastResult: data.lastResult ?? null,
    })
    .onConflictDoUpdate({
      target: [problemProgress.userId, problemProgress.problemId],
      set: {
        status: data.status,
        attempts: data.attempts ?? 0,
        lastResult: data.lastResult ?? null,
        updatedAt: new Date(),
      },
    });

  const updated = await db.query.problemProgress.findFirst({
    where: and(
      eq(problemProgress.userId, userId),
      eq(problemProgress.problemId, data.problemId),
    ),
  });

  return successResponse({
    status: updated?.status ?? data.status,
    attempts: updated?.attempts ?? data.attempts ?? 0,
    lastResult: updated?.lastResult ?? data.lastResult ?? null,
  });
}
