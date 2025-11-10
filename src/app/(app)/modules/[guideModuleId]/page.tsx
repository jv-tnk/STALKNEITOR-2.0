import { notFound } from "next/navigation";

import Link from "next/link";

import { eq } from "drizzle-orm";

import { getDb, modules as modulesTable, problems as problemsTable } from "@/lib/db";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ModuleProgressCard } from "@/components/modules/module-progress-card";
import { ProblemProgressTable } from "@/components/modules/problem-progress-table";

export default async function ModulePage({
  params,
}: {
  params: Promise<{ guideModuleId: string }>;
}) {
  const resolvedParams = await params;
  const db = getDb();
  const moduleData = await db.query.modules.findFirst({
    where: eq(modulesTable.guideModuleId, resolvedParams.guideModuleId),
  });

  if (!moduleData) {
    return notFound();
  }

  const moduleProblems = await db
    .select({
      id: problemsTable.id,
      uniqueId: problemsTable.uniqueId,
      name: problemsTable.name,
      url: problemsTable.url,
      source: problemsTable.source,
      difficulty: problemsTable.difficulty,
      tags: problemsTable.tags,
    })
    .from(problemsTable)
    .where(eq(problemsTable.guideModuleId, moduleData.guideModuleId))
    .orderBy(problemsTable.name);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-2">
        <Badge variant="outline">{moduleData.division}</Badge>
        <h1 className="text-3xl font-semibold">{moduleData.title}</h1>
        <p className="text-muted-foreground">
          Sincronizado automaticamente a partir do USACO Guide.
        </p>
        <div className="flex flex-wrap gap-3">
          <Button asChild>
            <Link href={moduleData.url} target="_blank" rel="noreferrer">
              Abrir no USACO Guide
            </Link>
          </Button>
        </div>
      </div>

      <ModuleProgressCard
        guideModuleId={moduleData.guideModuleId}
        totalProblems={moduleProblems.length}
      />

      <div className="rounded-md border p-4">
        <h2 className="text-xl font-semibold">Problemas ({moduleProblems.length})</h2>
        <ProblemProgressTable guideModuleId={moduleData.guideModuleId} problems={moduleProblems} />
      </div>
    </div>
  );
}
