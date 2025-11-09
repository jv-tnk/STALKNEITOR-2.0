import Link from "next/link";

import { fetchGuideCatalog } from "@/lib/usaco/guide";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";

const DIVISION_META: Record<
  string,
  {
    description: string;
    guideUrl: string;
  }
> = {
  General: {
    description: "Fundamentos, setup e boas práticas.",
    guideUrl: "https://usaco.guide/general",
  },
  Bronze: {
    description: "Primeiros tópicos de estruturas de dados e simulação.",
    guideUrl: "https://usaco.guide/bronze",
  },
  Silver: {
    description: "Grafos, prefixos, dois ponteiros e DP básica.",
    guideUrl: "https://usaco.guide/silver",
  },
  Gold: {
    description: "DP avançada, tree tricks e otimizações.",
    guideUrl: "https://usaco.guide/gold",
  },
  Platinum: {
    description: "Estruturas sofisticadas, flows e problemas de pesquisa.",
    guideUrl: "https://usaco.guide/platinum",
  },
  Advanced: {
    description: "Tópicos extras, ad-hoc e conteúdo convidado.",
    guideUrl: "https://usaco.guide/advanced",
  },
};

const DIVISION_ORDER = [
  "General",
  "Bronze",
  "Silver",
  "Gold",
  "Platinum",
  "Advanced",
] as const;

export default async function DivisionsPage() {
  const { modules } = await fetchGuideCatalog();
  const total = modules.length || 1;

  const grouped = DIVISION_ORDER.map((division) => {
    const entries = modules.filter((module) => module.division === division);
    const percentage = Math.round((entries.length / total) * 100);
    return {
      id: division.toLowerCase(),
      title: division,
      description: DIVISION_META[division]?.description ?? "",
      guideUrl: DIVISION_META[division]?.guideUrl,
      modules: entries.length,
      percentage,
    };
  }).filter((entry) => entry.modules > 0);

  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm uppercase tracking-wider text-muted-foreground">
          Catálogo
        </p>
        <h1 className="text-3xl font-semibold">Divisões</h1>
        <p className="text-muted-foreground">
          Explore módulos e problemas sincronizados diretamente do USACO Guide.
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {grouped.map((division) => (
          <Card key={division.id} id={`division-${division.id}`}>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>{division.title}</CardTitle>
                <Badge variant="secondary">
                  {division.modules} módulos
                </Badge>
              </div>
              <p className="text-sm text-muted-foreground">
                {division.description}
              </p>
            </CardHeader>
            <CardContent className="space-y-3">
              <div>
                <p className="text-xs text-muted-foreground">
                  Percentual no catálogo
                </p>
                <Progress value={division.percentage} />
                <p className="mt-1 text-xs text-muted-foreground">
                  {division.percentage}% dos módulos publicados
                </p>
              </div>
              {division.guideUrl && (
                <a
                  className="text-sm text-muted-foreground underline-offset-2 hover:underline"
                  href={division.guideUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  Abrir seção oficial ↗
                </a>
              )}
            </CardContent>
            <CardFooter>
              <Link
                className="text-sm text-primary"
                href={`/modules?division=${division.id}`}
              >
                Ver módulos →
              </Link>
            </CardFooter>
          </Card>
        ))}
      </div>
    </div>
  );
}
