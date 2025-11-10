import { NextRequest } from "next/server";
import { and, desc, eq } from "drizzle-orm";
import { z } from "zod";

import { errorResponse, successResponse } from "@/app/api/_lib/responses";
import { requireUser } from "@/lib/auth";
import { getDb, problemSolutions, problems, users } from "@/lib/db";
import { LANGUAGE_VALUES, detectLanguageFromContent } from "@/lib/constants/languages";

const createSolutionSchema = z.object({
  problemId: z.string().uuid(),
  guideModuleId: z.string().min(1),
  content: z.string().min(1),
  language: z.enum(LANGUAGE_VALUES).optional().default("auto"),
  isPublic: z.boolean().optional().default(true),
});

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const problemIdParam = url.searchParams.get("problemId");
  const scope = (url.searchParams.get("scope") ?? "public").toLowerCase();

  if (!problemIdParam) {
    return errorResponse(400, "Missing problemId parameter");
  }

  const problemIdParsed = z.string().uuid().safeParse(problemIdParam);
  if (!problemIdParsed.success) {
    return errorResponse(400, "Invalid problemId parameter");
  }

  let userId: string | null = null;
  if (scope === "mine") {
    try {
      userId = (await requireUser()).id;
    } catch {
      return errorResponse(401, "Unauthorized");
    }
  }

  const db = getDb();
  const rows = await db
    .select({
      id: problemSolutions.id,
      content: problemSolutions.content,
      isPublic: problemSolutions.isPublic,
      language: problemSolutions.language,
      createdAt: problemSolutions.createdAt,
      author: {
        id: users.id,
        name: users.name,
      },
    })
    .from(problemSolutions)
    .leftJoin(users, eq(problemSolutions.userId, users.id))
    .where(
      scope === "mine"
        ? and(eq(problemSolutions.userId, userId!), eq(problemSolutions.problemId, problemIdParsed.data))
        : and(eq(problemSolutions.isPublic, true), eq(problemSolutions.problemId, problemIdParsed.data)),
    )
    .orderBy(() => desc(problemSolutions.createdAt));

  return successResponse({ solutions: rows });
}

export async function POST(req: NextRequest) {
  const json = await req.json().catch(() => null);
  const parsed = createSolutionSchema.safeParse(json);

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
  const problemExists = await db.query.problems.findFirst({
    where: eq(problems.id, parsed.data.problemId),
  });

  if (!problemExists) {
    return errorResponse(404, "Problem not found");
  }

  const [solution] = await db
    .insert(problemSolutions)
    .values({
      userId,
      problemId: parsed.data.problemId,
      guideModuleId: parsed.data.guideModuleId,
      content: parsed.data.content,
      language:
        parsed.data.language && parsed.data.language !== "auto"
          ? parsed.data.language
          : detectLanguageFromContent(parsed.data.content),
      isPublic: parsed.data.isPublic ?? true,
    })
    .returning();

  return successResponse(solution, { status: 201 });
}
