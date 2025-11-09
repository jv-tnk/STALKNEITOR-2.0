import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";

const teams = [
  {
    id: "alpha",
    name: "Alpha Llamas",
    members: 4,
    visibility: "Privado",
  },
  {
    id: "public",
    name: "Divisão Bronze 2024",
    members: 12,
    visibility: "Público",
  },
];

export default function TeamsPage() {
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-sm uppercase tracking-wider text-muted-foreground">
            Times
          </p>
          <h1 className="text-3xl font-semibold">Organize seu squad</h1>
        </div>
        <Button>Criar time</Button>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {teams.map((team) => (
          <Card key={team.id}>
            <CardHeader>
              <CardTitle>{team.name}</CardTitle>
              <p className="text-sm text-muted-foreground">
                {team.members} membros · {team.visibility}
              </p>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Feed de marcos e leaderboard chegam aqui em breve.
              </p>
            </CardContent>
            <CardFooter>
              <Button asChild variant="outline">
                <Link href={`/teams/${team.id}`}>Abrir painel</Link>
              </Button>
            </CardFooter>
          </Card>
        ))}
      </div>
    </div>
  );
}
