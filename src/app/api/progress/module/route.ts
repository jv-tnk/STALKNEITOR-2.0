import { NextRequest } from "next/server";
import { and, count, eq } from "drizzle-orm";
import { z } from "zod";

import { errorResponse, successResponse } from "@/app/api/_lib/responses";
import { getDb, moduleProgress, problems, problemProgress } from "@/lib/db";
import { requireUser } from "@/lib/auth";
import { PROGRESS_STATUSES } from "@/lib/constants/progress";

const payloadSchema = z.object({
  guideModuleId: z.string().min(1),
  status: z.enum(PROGRESS_STATUSES),
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
    .insert(moduleProgress)
    .values({
      userId,
      guideModuleId: data.guideModuleId,
      status: data.status,
      percent: 0,
    })
    .onConflictDoUpdate({
      target: [moduleProgress.userId, moduleProgress.guideModuleId],
      set: {
        status: data.status,
        updatedAt: new Date(),
      },
    });

  const total = await db
    .select({ value: count() })
    .from(problems)
    .where(eq(problems.guideModuleId, data.guideModuleId));

  const done = await db
    .select({ value: count() })
    .from(problemProgress)
    .innerJoin(problems, eq(problemProgress.problemId, problems.id))
    .where(
      and(
        eq(problemProgress.userId, userId),
        eq(problems.guideModuleId, data.guideModuleId),
        eq(problemProgress.status, "done"),
      ),
    );

  const percent =
    total[0]?.value && total[0].value > 0
      ? Math.round(((done[0]?.value ?? 0) / total[0].value) * 100)
      : 0;

  await db
    .update(moduleProgress)
    .set({ percent })
    .where(
      and(
        eq(moduleProgress.userId, userId),
        eq(moduleProgress.guideModuleId, data.guideModuleId),
      ),
    );

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

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const moduleId = url.searchParams.get("module");

  if (!moduleId) {
    return errorResponse(400, "Missing module parameter");
  }

  let userId: string;
  try {
    userId = (await requireUser()).id;
  } catch {
    return errorResponse(401, "Unauthorized");
  }

  const db = getDb();
  const progress = await db.query.moduleProgress.findFirst({
    where: and(
      eq(moduleProgress.userId, userId),
      eq(moduleProgress.guideModuleId, moduleId),
    ),
  });

  if (progress) {
    return successResponse({
      status: progress.status,
      percent: progress.percent,
    });
  }

  const total = await db
    .select({ value: count() })
    .from(problems)
    .where(eq(problems.guideModuleId, moduleId));

  const done = await db
    .select({ value: count() })
    .from(problemProgress)
    .innerJoin(problems, eq(problemProgress.problemId, problems.id))
    .where(
      and(
        eq(problemProgress.userId, userId),
        eq(problems.guideModuleId, moduleId),
        eq(problemProgress.status, "done"),
      ),
    );

  const percent =
    total[0]?.value && total[0].value > 0
      ? Math.round(((done[0]?.value ?? 0) / total[0].value) * 100)
      : 0;

  return successResponse({
    status: "not_started",
    percent,
  });
}
