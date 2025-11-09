import { randomBytes } from "crypto";
import { z } from "zod";

import { errorResponse, successResponse } from "@/app/api/_lib/responses";
import { requireUser } from "@/lib/auth";
import { getDb, teamInvites } from "@/lib/db";

const payloadSchema = z.object({
  role: z.enum(["member", "admin"]).default("member"),
  expiresAt: z.string().datetime().optional(),
});

export async function POST(request: Request, { params }: { params: { teamId: string } }) {
  const body = await request.json().catch(() => null);
  const parsed = payloadSchema.safeParse(body);

  if (!parsed.success) {
    return errorResponse(400, "Invalid payload", parsed.error.format());
  }

  const currentUser = await requireUser().catch(() => null);
  if (!currentUser) {
    return errorResponse(401, "Unauthorized");
  }

  const db = getDb();

  const membership = await db.query.teamMembers.findFirst({
    where: (table, { and, eq }) =>
      and(eq(table.teamId, params.teamId), eq(table.userId, currentUser.id)),
  });

  if (!membership || !["owner", "admin"].includes(membership.role)) {
    return errorResponse(403, "You do not have permission to invite members");
  }

  const token = randomBytes(24).toString("hex");
  const [invite] = await db
    .insert(teamInvites)
    .values({
      teamId: params.teamId,
      token,
      role: parsed.data.role,
      expiresAt: parsed.data.expiresAt
        ? new Date(parsed.data.expiresAt)
        : undefined,
      createdBy: currentUser.id,
    })
    .returning();

  return successResponse({
    ...invite,
    url: `${process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000"}/teams/join/${token}`,
  });
}
