import { sql } from "drizzle-orm";

import { errorResponse, successResponse } from "@/app/api/_lib/responses";
import { requireUser } from "@/lib/auth";
import { getDb } from "@/lib/db";

export async function GET(_: Request, { params }: { params: { teamId: string } }) {
  try {
    await requireUser();
  } catch {
    return errorResponse(401, "Unauthorized");
  }

  const db = getDb();
  const leaderboard = await db.execute(sql`
    SELECT u.id,
           COALESCE(u.name, u.email) AS name,
           tm.role,
           COALESCE(SUM(CASE pp.status WHEN 'done' THEN 1 ELSE 0 END), 0) AS problems_done,
           COALESCE(MAX(pp.updated_at), NOW()) AS last_activity
    FROM team_members tm
    JOIN users u ON u.id = tm.user_id
    LEFT JOIN problem_progress pp ON pp.user_id = tm.user_id
    WHERE tm.team_id = ${params.teamId}
    GROUP BY u.id, tm.role
    ORDER BY problems_done DESC;
  `);

  return successResponse({ leaderboard: leaderboard.rows });
}
