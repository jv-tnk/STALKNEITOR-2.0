const phases = [
  {
    title: "R1 · MVP",
    goals: [
      "Auth por magic link via Supabase",
      "Catálogo navegável + registro de status e notas",
      "Times (CRUD, convites) e leaderboard básico",
      "ETL diário sincronizando módulos/problemas",
    ],
  },
  {
    title: "R2",
    goals: [
      "Heatmap de atividade e metas semanais",
      "Exportação CSV/JSON",
      "Melhorias no feed e notificações in-app",
    ],
  },
  {
    title: "R3",
    goals: [
      "Bot Discord/Telegram",
      "Badges por divisão",
      "Focus problems e alertas configuráveis",
    ],
  },
];

export default function RoadmapPage() {
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-10 py-10">
      <header className="space-y-3">
        <p className="text-sm uppercase tracking-wider text-muted-foreground">
          Roadmap
        </p>
        <h1 className="text-4xl font-semibold">Entrega do Stalkneitor 2.0</h1>
        <p className="text-muted-foreground">
          Sequência planejada para validar o MVP e evoluir com foco em motivação
          e colaboração entre squads do USACO Guide.
        </p>
      </header>
      <div className="space-y-8">
        {phases.map((phase) => (
          <div key={phase.title} className="rounded-lg border p-6">
            <h2 className="text-2xl font-semibold">{phase.title}</h2>
            <ul className="mt-4 list-disc space-y-1 pl-6 text-muted-foreground">
              {phase.goals.map((goal) => (
                <li key={goal}>{goal}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
