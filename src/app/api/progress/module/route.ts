import { NextRequest } from "next/server";
import { and, eq } from "drizzle-orm";
import { z } from "zod";

import { errorResponse, successResponse } from "@/app/api/_lib/responses";
import { getDb, moduleProgress } from "@/lib/db";
import { requireUser } from "@/lib/auth";
import { PROGRESS_STATUSES } from "@/lib/constants/progress";

const payloadSchema = z.object({
  guideModuleId: z.string().min(1),
  status: z.enum(PROGRESS_STATUSES),
  percent: z.number().int().min(0).max(100).optional(),
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
  const percent = data.percent ?? (data.status === "done" ? 100 : 0);

  await db
    .insert(moduleProgress)
    .values({
      userId,
      guideModuleId: data.guideModuleId,
      status: data.status,
      percent,
    })
    .onConflictDoUpdate({
      target: [moduleProgress.userId, moduleProgress.guideModuleId],
      set: {
        status: data.status,
        percent,
        updatedAt: new Date(),
      },
    });

  const updated = await db.query.moduleProgress.findFirst({
    where: and(
      eq(moduleProgress.userId, userId),
      eq(moduleProgress.guideModuleId, data.guideModuleId),
    ),
  });

  return successResponse({
    status: updated?.status ?? data.status,
    percent: updated?.percent ?? percent,
  });
}
