import { NextRequest } from "next/server";
import { eq } from "drizzle-orm";
import { z } from "zod";

import { errorResponse, successResponse } from "@/app/api/_lib/responses";
import { requireUser } from "@/lib/auth";
import { getDb, teamInvites, teamMembers } from "@/lib/db";

const payloadSchema = z.object({
  token: z.string().min(24),
});

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  const parsed = payloadSchema.safeParse(body);

  if (!parsed.success) {
    return errorResponse(400, "Invalid payload", parsed.error.format());
  }

  const user = await requireUser().catch(() => null);
  if (!user) {
    return errorResponse(401, "Unauthorized");
  }

  const db = getDb();
  const invite = await db.query.teamInvites.findFirst({
    where: eq(teamInvites.token, parsed.data.token),
  });

  if (!invite) {
    return errorResponse(404, "Invite not found");
  }

  if (invite.expiresAt && invite.expiresAt < new Date()) {
    return errorResponse(410, "Invite expired");
  }

  await db
    .insert(teamMembers)
    .values({
      teamId: invite.teamId,
      userId: user.id,
      role: invite.role ?? "member",
    })
    .onConflictDoNothing({
      target: [teamMembers.teamId, teamMembers.userId],
    });

  return successResponse({ joined: invite.teamId });
}
