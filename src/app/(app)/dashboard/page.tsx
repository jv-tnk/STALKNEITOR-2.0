import { eq } from "drizzle-orm";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { getDb, problemProgress, problems, users } from "@/lib/db";
import { getProblemWeight } from "@/lib/constants/scoring";

const stats = [
  { label: "Módulos feitos", value: "12", helper: "de 48" },
  { label: "Problemas resolvidos", value: "87", helper: "+5 esta semana" },
  { label: "Streak", value: "9 dias", helper: "meta: 30" },
];

const upcoming = [
  { title: "Bronze Prefix Sums", status: "Em andamento" },
  { title: "Silver Graphs I", status: "Próximo" },
  { title: "Platinum Range Queries", status: "Backlog" },
];

export default async function DashboardPage() {
  const leaderboard = await getLeaderboard();

  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <p className="text-sm uppercase tracking-wider text-muted-foreground">
          Visão geral
        </p>
        <div className="grid gap-4 md:grid-cols-3">
          {stats.map((stat) => (
            <Card key={stat.label}>
              <CardHeader className="pb-2">
                <p className="text-sm text-muted-foreground">{stat.label}</p>
                <CardTitle className="text-3xl">{stat.value}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground">{stat.helper}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Feed recente</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
              Integração com eventos ainda não configurada.
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Próximos focos</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {upcoming.map((item) => (
              <div key={item.title} className="rounded-md border p-3">
                <p className="font-medium">{item.title}</p>
                <p className="text-sm text-muted-foreground">{item.status}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm uppercase tracking-wider text-muted-foreground">
              Ranking
            </p>
            <h2 className="text-2xl font-semibold">Leaderboard geral</h2>
          </div>
        </div>
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-muted">
                <th className="px-4 py-2 text-left">Posição</th>
                <th className="px-4 py-2 text-left">Usuário</th>
                <th className="px-4 py-2 text-left">Tag</th>
                <th className="px-4 py-2 text-left">Pontos</th>
              </tr>
            </thead>
            <tbody>
              {leaderboard.length ? (
                leaderboard.map((entry, index) => (
                  <tr key={entry.userId} className="border-t">
                    <td className="px-4 py-2 font-semibold">#{index + 1}</td>
                    <td className="px-4 py-2">
                      <div className="font-medium">{entry.name ?? entry.email}</div>
                      <div className="text-xs text-muted-foreground">{entry.email}</div>
                    </td>
                    <td className="px-4 py-2">
                      <Badge variant="secondary">{entry.tag}</Badge>
                    </td>
                    <td className="px-4 py-2 font-semibold">{entry.points}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={4} className="px-4 py-6 text-center text-muted-foreground">
                    Ainda não há pontos registrados.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

async function getLeaderboard() {
  const db = getDb();
  const rows = await db
    .select({
      userId: problemProgress.userId,
      status: problemProgress.status,
      difficulty: problems.difficulty,
      name: users.name,
      email: users.email,
    })
    .from(problemProgress)
    .innerJoin(users, eq(problemProgress.userId, users.id))
    .innerJoin(problems, eq(problemProgress.problemId, problems.id))
    .where(eq(problemProgress.status, "done"));

  const scores = new Map<string, { userId: string; name: string | null; email: string; points: number }>();
  rows.forEach((row) => {
    const weight = getProblemWeight(row.difficulty);
    const existing = scores.get(row.userId) ?? {
      userId: row.userId,
      name: row.name,
      email: row.email ?? "",
      points: 0,
    };
    existing.points += weight;
    scores.set(row.userId, existing);
  });

  return Array.from(scores.values())
    .sort((a, b) => b.points - a.points)
    .slice(0, 10)
    .map((entry) => ({
      ...entry,
      tag: resolveTag(entry.points),
    }));
}

function resolveTag(points: number) {
  if (points >= 250) return "Platinum Legend";
  if (points >= 150) return "Gold Ace";
  if (points >= 90) return "Silver Slayer";
  if (points >= 40) return "Bronze Grinder";
  return "Rookie";
}
