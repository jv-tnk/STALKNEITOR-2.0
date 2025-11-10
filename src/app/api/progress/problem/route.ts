import { NextRequest } from "next/server";
import { and, count, eq } from "drizzle-orm";
import { z } from "zod";

import { errorResponse, successResponse } from "@/app/api/_lib/responses";
import { requireUser } from "@/lib/auth";
import { getDb, moduleProgress, problemProgress, problems } from "@/lib/db";
import { PROGRESS_STATUSES } from "@/lib/constants/progress";

const payloadSchema = z.object({
  problemId: z.string(), // accepts UUID or uniqueId
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

  const problemRecord = await resolveProblem(db, data.problemId);
  if (!problemRecord) {
    return errorResponse(404, "Problem not found");
  }

  await db
    .insert(problemProgress)
    .values({
      userId,
      problemId: problemRecord.id,
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
      eq(problemProgress.problemId, problemRecord.id),
    ),
  });

  await updateModulePercent(db, userId, problemRecord.guideModuleId);

  return successResponse({
    status: updated?.status ?? data.status,
    attempts: updated?.attempts ?? data.attempts ?? 0,
    lastResult: updated?.lastResult ?? data.lastResult ?? null,
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
  const entries = await db
    .select({
      problemId: problemProgress.problemId,
      problemUniqueId: problems.uniqueId,
      status: problemProgress.status,
      attempts: problemProgress.attempts,
      lastResult: problemProgress.lastResult,
      updatedAt: problemProgress.updatedAt,
    })
    .from(problemProgress)
    .innerJoin(problems, eq(problemProgress.problemId, problems.id))
    .where(
      and(eq(problemProgress.userId, userId), eq(problems.guideModuleId, moduleId)),
    );

  const progress = entries.reduce(
    (acc, curr) => {
      acc[curr.problemId] = {
        status: curr.status,
        attempts: curr.attempts,
        lastResult: curr.lastResult,
        updatedAt: curr.updatedAt,
      };
      return acc;
    },
    {} as Record<
      string,
      {
        status: string;
        attempts: number;
        lastResult: string | null;
        updatedAt: Date;
      }
    >,
  );

  return successResponse({ progress });
}

async function resolveProblem(db: ReturnType<typeof getDb>, identifier: string) {
  const asUuid = z.string().uuid().safeParse(identifier);
  if (asUuid.success) {
    const problem = await db.query.problems.findFirst({
      where: eq(problems.id, asUuid.data),
    });
    if (problem) return problem;
  }
  return db.query.problems.findFirst({
    where: eq(problems.uniqueId, identifier),
  });
}

async function updateModulePercent(
  db: ReturnType<typeof getDb>,
  userId: string,
  guideModuleId: string,
) {
  const total = await db
    .select({ value: count() })
    .from(problems)
    .where(eq(problems.guideModuleId, guideModuleId));

  const done = await db
    .select({ value: count() })
    .from(problemProgress)
    .innerJoin(problems, eq(problemProgress.problemId, problems.id))
    .where(
      and(
        eq(problemProgress.userId, userId),
        eq(problems.guideModuleId, guideModuleId),
        eq(problemProgress.status, "done"),
      ),
    );

  const percent =
    total[0]?.value && total[0].value > 0
      ? Math.round(((done[0]?.value ?? 0) / total[0].value) * 100)
      : 0;

  await db
    .insert(moduleProgress)
    .values({
      userId,
      guideModuleId,
      status: percent === 100 ? "done" : "in_progress",
      percent,
    })
    .onConflictDoUpdate({
      target: [moduleProgress.userId, moduleProgress.guideModuleId],
      set: {
        percent,
        ...(percent === 100 ? { status: "done" } : {}),
        updatedAt: new Date(),
      },
    });
}
