import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

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

export default function DashboardPage() {
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
    </div>
  );
}
