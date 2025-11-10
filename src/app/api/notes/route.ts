import { NextRequest } from "next/server";
import { and, desc, eq, type SQL } from "drizzle-orm";
import { z } from "zod";

import { errorResponse, successResponse } from "@/app/api/_lib/responses";
import { requireUser } from "@/lib/auth";
import { getDb, notes, users } from "@/lib/db";

const createNoteSchema = z.object({
  guideModuleId: z.string().min(1),
  problemId: z.string().uuid().optional().nullable(),
  content: z.string().min(1),
  visibility: z.enum(["private", "public"]).default("private").optional(),
});

export async function GET(req: NextRequest) {
  let userId: string;
  try {
    userId = (await requireUser()).id;
  } catch {
    return errorResponse(401, "Unauthorized");
  }

  const url = new URL(req.url);
  const moduleFilter = url.searchParams.get("module");
  const problemFilterParam = url.searchParams.get("problem");
  const scope = url.searchParams.get("scope") ?? "mine";
  const problemFilter = problemFilterParam
    ? z.string().uuid().safeParse(problemFilterParam)
    : null;

  const db = getDb();
  if (scope === "public") {
    const filters: SQL<unknown>[] = [eq(notes.visibility, "public")];

    if (moduleFilter) {
      filters.push(eq(notes.guideModuleId, moduleFilter));
    }
    if (problemFilter?.success) {
      filters.push(eq(notes.problemId, problemFilter.data));
    }

    const data = await db
      .select({
        id: notes.id,
        content: notes.content,
        visibility: notes.visibility,
        updatedAt: notes.updatedAt,
        author: {
          id: users.id,
          name: users.name,
        },
      })
      .from(notes)
      .leftJoin(users, eq(notes.userId, users.id))
      .where(combine(filters))
      .orderBy(() => desc(notes.updatedAt));

    return successResponse({ notes: data });
  }

  const filters: SQL<unknown>[] = [eq(notes.userId, userId)];

  if (moduleFilter) {
    filters.push(eq(notes.guideModuleId, moduleFilter));
  }

  if (problemFilter?.success) {
    filters.push(eq(notes.problemId, problemFilter.data));
  }

  const data = await db.query.notes.findMany({
    where: combine(filters),
    orderBy: (note) => desc(note.updatedAt),
  });

  return successResponse({ notes: data });
}

export async function POST(req: NextRequest) {
  const json = await req.json().catch(() => null);
  const parsed = createNoteSchema.safeParse(json);

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

  const [inserted] = await db
    .insert(notes)
    .values({
      userId,
      guideModuleId: data.guideModuleId,
      problemId: data.problemId ?? null,
      content: data.content,
      visibility: data.visibility ?? "private",
    })
    .returning();

  return successResponse(inserted, { status: 201 });
}

function combine(conditions: SQL<unknown>[]) {
  if (!conditions.length) return undefined;
  return conditions.slice(1).reduce(
    (acc, condition) => and(acc, condition),
    conditions[0],
  );
}
