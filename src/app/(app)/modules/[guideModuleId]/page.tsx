import { notFound } from "next/navigation";

import Link from "next/link";

import { fetchGuideCatalog } from "@/lib/usaco/guide";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export default async function ModulePage({
  params,
}: {
  params: Promise<{ guideModuleId: string }>;
}) {
  const resolvedParams = await params;
  const catalog = await fetchGuideCatalog();
  const moduleData = catalog.modules.find(
    (module) => module.guideModuleId === resolvedParams.guideModuleId,
  );

  if (!moduleData) {
    return notFound();
  }

  const moduleProblems = catalog.problems.filter(
    (problem) => problem.guideModuleId === moduleData.guideModuleId,
  );

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
          <Button variant="secondary">Adicionar nota</Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Problemas ({moduleProblems.length})</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[240px]">Problema</TableHead>
                  <TableHead>Fonte</TableHead>
                  <TableHead>Dificuldade</TableHead>
                  <TableHead>Tags</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {moduleProblems.map((problem) => (
                  <TableRow key={problem.id}>
                    <TableCell className="font-medium">
                      <a
                        href={problem.url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-primary underline-offset-2 hover:underline"
                      >
                        {problem.name}
                      </a>
                    </TableCell>
                    <TableCell>{problem.source ?? "—"}</TableCell>
                    <TableCell>{problem.difficulty ?? "—"}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {problem.tags.length ? (
                          problem.tags.map((tag) => (
                            <Badge variant="secondary" key={tag}>
                              {tag}
                            </Badge>
                          ))
                        ) : (
                          <span className="text-xs text-muted-foreground">
                            Sem tags
                          </span>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
                {!moduleProblems.length && (
                  <TableRow>
                    <TableCell
                      colSpan={4}
                      className="text-center text-sm text-muted-foreground"
                    >
                      Nenhum problema listado para este módulo.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
