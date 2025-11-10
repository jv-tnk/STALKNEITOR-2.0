import Link from "next/link";
import { eq } from "drizzle-orm";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getDb, modules, problemProgress, problems, users } from "@/lib/db";
import { getProblemWeight } from "@/lib/constants/scoring";

const DIVISIONS = ["General", "Bronze", "Silver", "Gold", "Platinum", "Advanced"] as const;

type SearchParams = {
  division?: string;
  tag?: string;
};

export default async function RankingsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const divisionFilter = params.division && params.division !== "all" ? params.division : undefined;
  const tagFilter = params.tag?.trim().toLowerCase() || undefined;

  const rows = await fetchProgressRows();
  const leaderboard = computeLeaderboard(rows, { division: divisionFilter, tag: tagFilter });
  const divisionBoards = DIVISIONS.map((division) => ({
    division,
    entries: computeLeaderboard(rows, { division }),
  }));

  return (
    <div className="space-y-8">
      <div>
        <p className="text-sm uppercase tracking-wider text-muted-foreground">Ranking</p>
        <h1 className="text-3xl font-semibold">Leaderboard</h1>
        <p className="text-muted-foreground">Filtre por divisão ou tag para encontrar rivais à altura.</p>
      </div>

      <form className="flex flex-wrap items-end gap-3" action="/rankings" method="get">
        <label className="flex flex-col gap-1 text-sm font-medium">
          Divisão
          <select
            name="division"
            defaultValue={divisionFilter ?? "all"}
            className="rounded border border-input bg-background px-3 py-2 text-sm"
          >
            <option value="all">Todas</option>
            {DIVISIONS.map((division) => (
              <option key={division} value={division}>
                {division}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium">
          Tag
          <input
            type="text"
            name="tag"
            placeholder="ex.: dp"
            defaultValue={params.tag ?? ""}
            className="rounded border border-input bg-background px-3 py-2 text-sm"
          />
        </label>
        <Button type="submit">Filtrar</Button>
        <Button asChild variant="ghost" type="button">
          <Link href="/rankings">Limpar</Link>
        </Button>
      </form>

      <Card>
        <CardHeader>
          <CardTitle>
            Ranking {divisionFilter ? `- ${divisionFilter}` : tagFilter ? `- tag ${tagFilter}` : "geral"}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <LeaderboardTable entries={leaderboard} />
        </CardContent>
      </Card>

      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-semibold">Top por divisão</h2>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {divisionBoards.map((board) => (
            <Card key={board.division}>
              <CardHeader>
                <CardTitle>{board.division}</CardTitle>
              </CardHeader>
              <CardContent>
                <LeaderboardTable entries={board.entries.slice(0, 5)} compact />
              </CardContent>
            </Card>
          ))}
        </div>
      </section>
    </div>
  );
}

function LeaderboardTable({ entries, compact = false }: { entries: LeaderboardEntry[]; compact?: boolean }) {
  if (!entries.length) return <p className="text-sm text-muted-foreground">Sem dados ainda.</p>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="bg-muted">
            <th className="px-3 py-2">#</th>
            <th className="px-3 py-2">Usuário</th>
            {!compact && <th className="px-3 py-2">Badge</th>}
            <th className="px-3 py-2">Pontos</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry, idx) => (
            <tr key={entry.userId} className="border-b last:border-none">
              <td className="px-3 py-2 font-semibold">#{idx + 1}</td>
              <td className="px-3 py-2">
                <div className="font-medium">{entry.handle ? `@${entry.handle}` : entry.name ?? entry.email}</div>
                {!compact && <p className="text-xs text-muted-foreground">{entry.name ?? entry.email}</p>}
              </td>
              {!compact && (
                <td className="px-3 py-2">
                  <Badge variant="secondary">{entry.badge}</Badge>
                </td>
              )}
              <td className="px-3 py-2 font-semibold">{entry.points}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

async function fetchProgressRows() {
  const db = getDb();
  return db
    .select({
      userId: problemProgress.userId,
      name: users.name,
      email: users.email,
      handle: users.handle,
      difficulty: problems.difficulty,
      division: modules.division,
      tags: problems.tags,
    })
    .from(problemProgress)
    .innerJoin(users, eq(problemProgress.userId, users.id))
    .innerJoin(problems, eq(problemProgress.problemId, problems.id))
    .leftJoin(modules, eq(modules.guideModuleId, problems.guideModuleId))
    .where(eq(problemProgress.status, "done"));
}

function computeLeaderboard(
  rows: Awaited<ReturnType<typeof fetchProgressRows>>,
  filters: { division?: string; tag?: string }
) {
  const filtered = rows.filter((row) => {
    if (filters.division && row.division !== filters.division) return false;
    if (filters.tag) {
      const tags = row.tags ?? [];
      if (!tags.some((tag) => tag.toLowerCase() === filters.tag)) return false;
    }
    return true;
  });

  const scores = new Map<string, LeaderboardEntry>();
  filtered.forEach((row) => {
    const weight = getProblemWeight(row.difficulty);
    const existing = scores.get(row.userId) ?? {
      userId: row.userId,
      name: row.name,
      email: row.email ?? "",
      handle: row.handle ?? undefined,
      points: 0,
      badge: "Rookie",
    };
    existing.points += weight;
    scores.set(row.userId, existing);
  });

  return Array.from(scores.values())
    .map((entry) => ({ ...entry, badge: resolveBadge(entry.points) }))
    .sort((a, b) => b.points - a.points)
    .slice(0, 20);
}

function resolveBadge(points: number) {
  if (points >= 250) return "Platinum Legend";
  if (points >= 150) return "Gold Ace";
  if (points >= 90) return "Silver Slayer";
  if (points >= 40) return "Bronze Grinder";
  return "Rookie";
}

type LeaderboardEntry = {
  userId: string;
  name: string | null;
  email: string;
  handle?: string;
  points: number;
  badge: string;
};
