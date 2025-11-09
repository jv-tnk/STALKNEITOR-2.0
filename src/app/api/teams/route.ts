import { NextRequest } from "next/server";
import { z } from "zod";

import { errorResponse, successResponse } from "@/app/api/_lib/responses";
import { requireUser } from "@/lib/auth";
import { getDb, teamMembers, teams } from "@/lib/db";

const payloadSchema = z.object({
  name: z.string().min(3),
  isPublic: z.boolean().default(false),
});

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  const parsed = payloadSchema.safeParse(body);

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

  const [team] = await db
    .insert(teams)
    .values({
      name: data.name,
      ownerId: userId,
      isPublic: data.isPublic,
    })
    .returning();

  await db.insert(teamMembers).values({
    teamId: team.id,
    userId,
    role: "owner",
  });

  return successResponse(team, { status: 201 });
}
