import { config } from "dotenv";

config({ path: ".env.local" });

import { fetchGuideCatalog } from "../src/lib/usaco/guide";
import { getDb, modules, problems } from "../src/lib/db";

async function main() {
  const catalog = await fetchGuideCatalog({ force: true });
  const db = getDb();

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

  console.log(
    `USACO Guide sync completed. Modules: ${modulesUpserted}, Problems: ${problemsUpserted}`,
  );
}

main().catch((error) => {
  console.error("Failed to sync USACO Guide", error);
  process.exit(1);
});
