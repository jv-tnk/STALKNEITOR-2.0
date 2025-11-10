"use client";

import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PROGRESS_STATUSES, PROGRESS_STATUS_LABEL } from "@/lib/constants/progress";

const statusOptions = PROGRESS_STATUSES;

export function ModuleProgressCard({
  guideModuleId,
  totalProblems,
}: {
  guideModuleId: string;
  totalProblems: number;
}) {
  const queryClient = useQueryClient();

  const { data } = useQuery<{ status: string; percent: number }>({
    queryKey: ["module-progress", guideModuleId],
    queryFn: async () => {
      const res = await fetch(`/api/progress/module?module=${guideModuleId}`);
      if (!res.ok) throw new Error("Erro ao carregar progresso");
      return res.json();
    },
    initialData: { status: "not_started", percent: 0 },
  });

  const problemProgressQuery = useQuery<{ progress: Record<string, { status: string }> }>({
    queryKey: ["problem-progress", guideModuleId],
    queryFn: async () => {
      const res = await fetch(`/api/progress/problem?module=${guideModuleId}`);
      if (!res.ok) throw new Error("Erro ao carregar progresso dos problemas");
      return res.json();
    },
    initialData: { progress: {} },
  });

  const doneCount = useMemo(() => {
    return Object.values(problemProgressQuery.data?.progress ?? {}).filter((entry) => entry.status === "done").length;
  }, [problemProgressQuery.data]);

  const computedPercent = totalProblems ? Math.round((doneCount / totalProblems) * 100) : 0;

  const mutation = useMutation({
    mutationFn: async (status: string) => {
      const res = await fetch("/api/progress/module", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ guideModuleId, status, percent: computedPercent }),
      });
      if (!res.ok) {
        throw new Error("Não foi possível salvar o progresso do módulo");
      }
      return res.json();
    },
    onSuccess() {
      queryClient.invalidateQueries({ queryKey: ["module-progress", guideModuleId] });
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Progresso do módulo</CardTitle>
        <CardDescription>Defina seu status e mantenha notas rápidas.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <label className="flex flex-col gap-2 text-sm">
          Status
          <select
            value={data?.status ?? "not_started"}
            onChange={(event) => mutation.mutate(event.target.value)}
            disabled={mutation.isPending}
            className="w-full rounded border border-input bg-background px-2 py-1 text-sm"
          >
            {statusOptions.map((status) => (
              <option key={status} value={status}>
                {PROGRESS_STATUS_LABEL[status]}
              </option>
            ))}
          </select>
        </label>
        <div className="space-y-2 text-sm">
          <div className="flex items-center justify-between text-xs uppercase tracking-wide text-muted-foreground">
            <span>Percentual concluído</span>
            <span>{computedPercent}%</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-primary/20">
            <div
              className="h-full rounded-full bg-gradient-to-r from-emerald-400 via-sky-400 to-indigo-500 transition-all"
              style={{ width: `${computedPercent}%` }}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            {doneCount} / {totalProblems} problemas marcados como “Done”.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            onClick={() => mutation.mutate(data?.status ?? "not_started")}
            disabled={mutation.isPending}
          >
            Salvar alterações
          </Button>
          <Button variant="outline" asChild>
            <Link href={`/notes?module=${guideModuleId}`}>Gerenciar notas</Link>
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
