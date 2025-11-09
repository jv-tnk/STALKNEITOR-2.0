import { errorResponse, successResponse } from "@/app/api/_lib/responses";
import { getDb, modules, problems } from "@/lib/db";
import { fetchGuideCatalog } from "@/lib/usaco/guide";

export async function POST(req: Request) {
  const headerSecret = req.headers.get("x-etl-secret");
  const expectedSecret = process.env.ETL_SECRET;

  if (!expectedSecret || headerSecret !== expectedSecret) {
    return errorResponse(401, "Unauthorized");
  }

  const catalog = await fetchGuideCatalog({ force: true });

  let db: ReturnType<typeof getDb> | null = null;
  try {
    db = getDb();
  } catch (error) {
    console.warn("DATABASE_URL not configured; skipping persistence", error);
  }

  if (!db) {
    return successResponse({
      commitSha: catalog.commitSha,
      modulesDiscovered: catalog.modules.length,
      problemsDiscovered: catalog.problems.length,
      persisted: false,
    });
  }

  let modulesUpserted = 0;
  for (const moduleMeta of catalog.modules) {
    await db
      .insert(modules)
      .values({
        guideModuleId: moduleMeta.guideModuleId,
        title: moduleMeta.title,
        division: moduleMeta.division,
        orderIndex: moduleMeta.orderIndex,
        url: moduleMeta.url,
        guideVersion: catalog.commitSha,
      })
      .onConflictDoUpdate({
        target: modules.guideModuleId,
        set: {
          title: moduleMeta.title,
          division: moduleMeta.division,
          orderIndex: moduleMeta.orderIndex,
          url: moduleMeta.url,
          guideVersion: catalog.commitSha,
          updatedAt: new Date(),
        },
      });
    modulesUpserted += 1;
  }

  let problemsUpserted = 0;
  for (const problem of catalog.problems) {
    await db
      .insert(problems)
      .values({
        uniqueId: problem.uniqueId,
        name: problem.name,
        url: problem.url,
        source: problem.source ?? undefined,
        difficulty: problem.difficulty ?? undefined,
        tags: problem.tags,
        guideModuleId: problem.guideModuleId,
      })
      .onConflictDoUpdate({
        target: problems.uniqueId,
        set: {
          name: problem.name,
          url: problem.url,
          source: problem.source ?? undefined,
          difficulty: problem.difficulty ?? undefined,
          tags: problem.tags,
          guideModuleId: problem.guideModuleId,
          updatedAt: new Date(),
        },
      });
    problemsUpserted += 1;
  }

  return successResponse({
    commitSha: catalog.commitSha,
    modulesUpserted,
    problemsUpserted,
    persisted: true,
  });
}
