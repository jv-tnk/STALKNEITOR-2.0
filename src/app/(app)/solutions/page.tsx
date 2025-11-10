import { and, desc, eq } from "drizzle-orm";
import Link from "next/link";

import { requireUser } from "@/lib/auth";
import { getDb, problems, problemSolutions, users } from "@/lib/db";
import { SolutionForm } from "@/components/solutions/solution-form";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CODE_LANGUAGES } from "@/lib/constants/languages";

export default async function SolutionsPage({
  searchParams,
}: {
  searchParams: Promise<{ problemId?: string; scope?: string }>;
}) {
  const params = await searchParams;
  const problemId = params.problemId;
  const scope = params.scope === "mine" ? "mine" : "public";

  if (!problemId) {
    return <p className="text-muted-foreground">Selecione um problema para ver as soluções.</p>;
  }

  const user = await requireUser();
  const db = getDb();

  const problemRecord = await db.query.problems.findFirst({ where: eq(problems.id, problemId) });
  if (!problemRecord) {
    return <p className="text-muted-foreground">Problema não encontrado.</p>;
  }

  const solutions = await db
    .select({
      id: problemSolutions.id,
      content: problemSolutions.content,
      language: problemSolutions.language,
      isPublic: problemSolutions.isPublic,
      createdAt: problemSolutions.createdAt,
      authorName: users.name,
    })
    .from(problemSolutions)
    .leftJoin(users, eq(problemSolutions.userId, users.id))
    .where(
      scope === "mine"
        ? and(eq(problemSolutions.userId, user.id), eq(problemSolutions.problemId, problemId))
        : and(eq(problemSolutions.isPublic, true), eq(problemSolutions.problemId, problemId)),
    )
    .orderBy(desc(problemSolutions.createdAt));

  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm uppercase tracking-wider text-muted-foreground">Soluções</p>
        <h1 className="text-3xl font-semibold">{problemRecord.name}</h1>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant={scope === "public" ? "default" : "outline"} asChild>
            <Link href={`/solutions?problemId=${problemId}&scope=public`}>Públicas</Link>
          </Button>
          <Button variant={scope === "mine" ? "default" : "outline"} asChild>
            <Link href={`/solutions?problemId=${problemId}&scope=mine`}>Minhas soluções</Link>
          </Button>
        </div>
      </div>

      <SolutionForm guideModuleId={problemRecord.guideModuleId} problemId={problemRecord.id} />

      <section className="space-y-3">
        {solutions.length ? (
          solutions.map((solution) => (
            <article key={solution.id} className="space-y-3 rounded-lg border p-4">
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>{new Date(solution.createdAt).toLocaleString()}</span>
                <Badge variant={solution.isPublic ? "secondary" : "outline"}>
                  {solution.isPublic ? "Pública" : "Privada"}
                </Badge>
              </div>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Badge variant="outline">
                  {CODE_LANGUAGES.find((lang) => lang.value === solution.language)?.label ?? "Texto"}
                </Badge>
                <span>por {solution.authorName ?? "Anônimo"}</span>
              </div>
              <pre className="overflow-x-auto rounded-lg bg-muted p-4 text-sm font-mono text-foreground">
                {solution.content}
              </pre>
            </article>
          ))
        ) : (
          <p className="text-muted-foreground">Nenhuma solução ainda.</p>
        )}
      </section>
    </div>
  );
}
