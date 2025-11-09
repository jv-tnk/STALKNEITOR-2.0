import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const features = [
  {
    title: "Catálogo sincronizado",
    description:
      "Importa módulos e problemas do USACO Guide diariamente via ETL protegido.",
  },
  {
    title: "Controle de progresso",
    description:
      "Marque problemas como not_started, in_progress, skipped ou done e acompanhe o % por módulo.",
  },
  {
    title: "Times e leaderboard",
    description:
      "Monte times, convide colegas e compare streaks e pontuação semanal ponderada por dificuldade.",
  },
  {
    title: "Notas privadas",
    description:
      "Salve snippets por módulo/problema, com visibilidade opcional para o time.",
  },
];

export default function Home() {
  return (
    <div className="mx-auto flex min-h-screen w-full max-w-5xl flex-col gap-12 px-4 py-16">
      <section className="flex flex-col gap-6 text-center">
        <Badge className="mx-auto w-fit" variant="secondary">
          MVP em andamento
        </Badge>
        <h1 className="text-balance text-4xl font-semibold leading-tight sm:text-5xl">
          Radar de treino para o{" "}
          <span className="text-primary">USACO Guide</span>
        </h1>
        <p className="mx-auto max-w-2xl text-balance text-lg text-muted-foreground">
          O Stalkneitor 2.0 centraliza progresso individual e em equipe, notas,
          rankings e logs de atividade para manter sua equipe motivada rumo ao
          USACO.
        </p>
        <div className="flex flex-wrap items-center justify-center gap-3">
          <Button size="lg" asChild>
            <Link href="/dashboard">Ir para o app</Link>
          </Button>
          <Button size="lg" variant="outline" asChild>
            <Link href="/docs/roadmap">Ver roadmap</Link>
          </Button>
        </div>
      </section>

      <section className="grid gap-4 sm:grid-cols-2">
        {features.map((feature) => (
          <Card key={feature.title}>
            <CardHeader>
              <CardTitle>{feature.title}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                {feature.description}
              </p>
            </CardContent>
          </Card>
        ))}
      </section>
    </div>
  );
}
