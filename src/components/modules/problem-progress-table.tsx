"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PROGRESS_STATUS_LABEL, PROGRESS_STATUSES } from "@/lib/constants/progress";
import Link from "next/link";
import { cn } from "@/lib/utils";

const statusOptions = PROGRESS_STATUSES;

type Problem = {
  id: string;
  uniqueId: string;
  name: string;
  url: string;
  source: string | null;
  difficulty: string | null;
  tags: string[];
};

const difficultyColors: Record<string, string> = {
  "Very Easy": "bg-emerald-100 text-emerald-800",
  Easy: "bg-lime-100 text-lime-800",
  Medium: "bg-amber-100 text-amber-800",
  Hard: "bg-orange-100 text-orange-800",
  "Very Hard": "bg-rose-100 text-rose-800",
  default: "bg-slate-100 text-slate-800",
};

export function ProblemProgressTable({ guideModuleId, problems }: { guideModuleId: string; problems: Problem[] }) {
  const queryClient = useQueryClient();
  const [selectedStatus, setSelectedStatus] = useState<Record<string, string>>({});

  const { data, isLoading } = useQuery<{ progress: Record<string, { status: string }>}>({
    queryKey: ["problem-progress", guideModuleId],
    queryFn: async () => {
      const res = await fetch(`/api/progress/problem?module=${guideModuleId}`);
      if (!res.ok) throw new Error("Erro ao carregar progresso dos problemas");
      return res.json();
    },
    initialData: { progress: {} },
  });

  const mutation = useMutation({
    mutationFn: async ({ problemId, status }: { problemId: string; status: string }) => {
      const res = await fetch("/api/progress/problem", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ problemId, status }),
      });
      if (!res.ok) throw new Error("Erro ao atualizar progresso do problema");
      return res.json();
    },
    onSuccess() {
      queryClient.invalidateQueries({ queryKey: ["problem-progress", guideModuleId] });
    },
  });

  const rows = useMemo(() => problems, [problems]);

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Carregando progresso...</p>;
  }

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-[240px]">Problema</TableHead>
            <TableHead>Fonte</TableHead>
            <TableHead>Dificuldade</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Tags</TableHead>
            <TableHead className="text-right">Ações</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((problem) => {
            const currentStatus = selectedStatus[problem.id] ?? data.progress[problem.id]?.status ?? "not_started";

            return (
              <TableRow key={problem.id} className="border-white/5 bg-white/5 odd:bg-white/0 transition hover:bg-white/10">
                <TableCell className="font-medium">
                  <a className="text-primary underline-offset-2 hover:underline" href={problem.url} target="_blank" rel="noreferrer">
                    {problem.name}
                  </a>
                </TableCell>
                <TableCell>{problem.source ?? "—"}</TableCell>
                <TableCell>
                  <Badge
                    variant="outline"
                    className={cn(
                      "border-none px-3 py-1 text-xs font-semibold",
                      difficultyColors[problem.difficulty ?? ""] ??
                        difficultyColors.default,
                    )}
                  >
                    {problem.difficulty ?? "—"}
                  </Badge>
                </TableCell>
                <TableCell>
                  <select
                    value={currentStatus}
                    onChange={(event) => {
                      setSelectedStatus((prev) => ({ ...prev, [problem.id]: event.target.value }));
                      mutation.mutate({ problemId: problem.id, status: event.target.value });
                    }}
                    className="rounded border border-input bg-background px-2 py-1 text-sm"
                  >
                    {statusOptions.map((status) => (
                      <option key={status} value={status}>
                        {PROGRESS_STATUS_LABEL[status]}
                      </option>
                    ))}
                  </select>
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    {problem.tags?.length ? (
                      problem.tags.map((tag) => (
                        <Badge
                          key={tag}
                          variant="secondary"
                          className="border-none bg-slate-100 text-slate-800"
                        >
                          {tag}
                        </Badge>
                      ))
                    ) : (
                      <span className="text-xs text-muted-foreground">Sem tags</span>
                    )}
                  </div>
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex flex-row gap-2 justify-end">
                    <Button variant="outline" size="sm" asChild>
                      <Link href={`/solutions?problemId=${problem.id}`}>
                        Soluções
                      </Link>
                    </Button>
                    <Button variant="ghost" size="sm" asChild>
                      <Link href={`/notes?module=${guideModuleId}&problem=${problem.id}`}>
                        Notas
                      </Link>
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
