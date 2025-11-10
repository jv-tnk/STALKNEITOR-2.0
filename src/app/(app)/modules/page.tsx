import Link from "next/link";

import { fetchGuideCatalog } from "@/lib/usaco/guide";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

const DIVISION_LABELS = {
  general: "General",
  bronze: "Bronze",
  silver: "Silver",
  gold: "Gold",
  platinum: "Platinum",
  advanced: "Advanced",
} as const;

type SearchParams = {
  division?: string;
  q?: string;
};

export default async function ModulesPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const resolvedParams = await searchParams;
  const { modules } = await fetchGuideCatalog();
  const divisionParam = resolvedParams.division?.toLowerCase();
  const query = resolvedParams.q?.trim().toLowerCase();

  const filtered = modules.filter((module) => {
    const matchesDivision = divisionParam
      ? module.division.toLowerCase() === divisionParam
      : true;
    const matchesQuery = query
      ? module.title.toLowerCase().includes(query) ||
        module.guideModuleId.toLowerCase().includes(query)
      : true;
    return matchesDivision && matchesQuery;
  });

  const currentDivisionLabel = divisionParam
    ? DIVISION_LABELS[divisionParam as keyof typeof DIVISION_LABELS] ?? ""
    : "";

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <p className="text-sm uppercase tracking-wider text-muted-foreground">
          Catálogo do USACO Guide
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-3xl font-semibold">Módulos</h1>
          {currentDivisionLabel && <Badge variant="secondary">{currentDivisionLabel}</Badge>}
        </div>
        <p className="text-muted-foreground">
          Clique em um módulo para abrir detalhes, problemas e notas.
        </p>
      </header>

      <form className="flex flex-wrap items-center gap-3" action="/modules" method="get">
        <Input
          name="q"
          defaultValue={resolvedParams.q ?? ""}
          placeholder="Buscar por título ou slug"
          className="max-w-sm"
        />
        <Input
          name="division"
          defaultValue={divisionParam ?? ""}
          placeholder="Filtrar por divisão (ex.: bronze)"
          className="max-w-xs"
        />
        <Button type="submit" variant="outline">
          Aplicar filtros
        </Button>
      </form>

      <div className="grid gap-4">
        {filtered.map((module) => (
          <Card key={module.guideModuleId}>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle className="text-xl">{module.title}</CardTitle>
                <p className="text-sm text-muted-foreground">
                  {module.division} · ordem {module.orderIndex}
                </p>
              </div>
              <Badge variant="outline">{module.guideModuleId}</Badge>
            </CardHeader>
            <CardContent className="flex flex-wrap items-center gap-3">
              <Link className="text-primary" href={`/modules/${module.guideModuleId}`}>
                Abrir módulo →
              </Link>
              <a
                className="text-sm text-muted-foreground underline-offset-2 hover:underline"
                href={module.url}
                target="_blank"
                rel="noreferrer"
              >
                Ver no USACO Guide
              </a>
            </CardContent>
          </Card>
        ))}

        {!filtered.length && (
          <Card>
            <CardContent className="py-10 text-center text-muted-foreground">
              Nenhum módulo encontrado com os filtros atuais.
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
