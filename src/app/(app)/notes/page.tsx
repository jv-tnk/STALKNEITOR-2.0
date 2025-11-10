import { and, desc, eq, type SQL } from "drizzle-orm";
import Link from "next/link";

import { requireUser } from "@/lib/auth";
import { getDb, modules, notes, problems, users } from "@/lib/db";
import { NoteForm } from "@/components/notes/note-form";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export default async function NotesPage({
  searchParams,
}: {
  searchParams: Promise<{ module?: string; problem?: string; scope?: string }>;
}) {
  const params = await searchParams;
  const moduleId = params.module;
  const problemId = params.problem;
  const scope = params.scope === "public" ? "public" : "mine";

  const user = await requireUser();
  const db = getDb();

  const moduleRecord = moduleId
    ? await db.query.modules.findFirst({ where: eq(modules.guideModuleId, moduleId) })
    : null;
  const problemRecord = problemId
    ? await db.query.problems.findFirst({ where: eq(problems.id, problemId) })
    : null;

  const filters: SQL[] = [];
  if (moduleId) filters.push(eq(notes.guideModuleId, moduleId));
  if (problemId) filters.push(eq(notes.problemId, problemId));

  const buildWhere = (additional: SQL[]) => {
    const clauses = [...additional, ...filters];
    if (clauses.length === 1) return clauses[0];
    return and(...clauses);
  };

  const noteList = await db
    .select({
      id: notes.id,
      content: notes.content,
      visibility: notes.visibility,
      updatedAt: notes.updatedAt,
      authorName: users.name,
      problemName: problems.name,
      moduleTitle: modules.title,
    })
    .from(notes)
    .leftJoin(users, eq(notes.userId, users.id))
    .leftJoin(problems, eq(notes.problemId, problems.id))
    .leftJoin(modules, eq(notes.guideModuleId, modules.guideModuleId))
    .where(scope === "public" ? buildWhere([eq(notes.visibility, "public")]) : buildWhere([eq(notes.userId, user.id)]))
    .orderBy(desc(notes.updatedAt));

  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm uppercase tracking-wider text-muted-foreground">Notas</p>
        <h1 className="text-3xl font-semibold">
          {problemRecord?.name ?? moduleRecord?.title ?? "Minhas notas"}
        </h1>
        {moduleRecord && (
          <p className="text-muted-foreground">Módulo: {moduleRecord.title}</p>
        )}
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant={scope === "mine" ? "default" : "outline"} asChild>
            <Link href={buildNotesHref({ moduleId, problemId, scope: "mine" })}>
              Minhas notas
            </Link>
          </Button>
          <Button variant={scope === "public" ? "default" : "outline"} asChild>
            <Link href={buildNotesHref({ moduleId, problemId, scope: "public" })}>
              Notas públicas
            </Link>
          </Button>
        </div>
      </div>

      {moduleId && scope !== "public" && <NoteForm guideModuleId={moduleId} problemId={problemId} />}

      <section className="space-y-3">
        {noteList.length ? (
          noteList.map((note) => (
            <article key={note.id} className="rounded-lg border p-4">
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>{note.problemName ?? note.moduleTitle ?? "Nota"}</span>
                <Badge variant={note.visibility === "public" ? "secondary" : "outline"}>
                  {note.visibility === "public" ? "Pública" : "Privada"}
                </Badge>
              </div>
              <p className="mt-3 whitespace-pre-wrap text-sm text-foreground">{note.content}</p>
              {scope === "public" && (
                <p className="mt-2 text-xs text-muted-foreground">por {note.authorName ?? "Anônimo"}</p>
              )}
            </article>
          ))
        ) : (
          <p className="text-muted-foreground">Nenhuma nota para este contexto.</p>
        )}
      </section>

    </div>
  );
}

function buildNotesHref({ moduleId, problemId, scope }: { moduleId?: string | null; problemId?: string | null; scope: string }) {
  const params = new URLSearchParams();
  if (moduleId) params.set("module", moduleId);
  if (problemId) params.set("problem", problemId);
  params.set("scope", scope);
  const query = params.toString();
  return `/notes${query ? `?${query}` : ""}`;
}
