import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const leaderboard = [
  { name: "João", score: 38, streak: 9 },
  { name: "Marina", score: 22, streak: 5 },
  { name: "Ava", score: 15, streak: 3 },
];

export default function TeamPage({ params }: { params: { teamId: string } }) {
  return (
    <div className="space-y-6">
      <header>
        <p className="text-sm uppercase tracking-wider text-muted-foreground">
          Team #{params.teamId}
        </p>
        <h1 className="text-3xl font-semibold">Painel do time</h1>
        <p className="text-muted-foreground">
          Rankings semanais, streaks e feed de marcos sincronizados das ações
          dos membros.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Leaderboard semanal</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Posição</TableHead>
                <TableHead>Nome</TableHead>
                <TableHead>Pontuação</TableHead>
                <TableHead>Streak</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {leaderboard.map((entry, index) => (
                <TableRow key={entry.name}>
                  <TableCell>{index + 1}</TableCell>
                  <TableCell>{entry.name}</TableCell>
                  <TableCell>{entry.score}</TableCell>
                  <TableCell>{entry.streak} dias</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
