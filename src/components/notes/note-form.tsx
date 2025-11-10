"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export function NoteForm({
  guideModuleId,
  problemId,
}: {
  guideModuleId: string;
  problemId?: string;
}) {
  const router = useRouter();
  const [content, setContent] = useState("");
  const [visibility, setVisibility] = useState<"private" | "public">("private");
  const [isPending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    startTransition(async () => {
      const res = await fetch("/api/notes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ guideModuleId, problemId, content, visibility }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body.error ?? "Não foi possível salvar a nota.");
        return;
      }

      setContent("");
      setVisibility("private");
      router.refresh();
    });
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <Textarea
        value={content}
        onChange={(event) => setContent(event.target.value)}
        placeholder="Nova nota..."
        rows={4}
      />
      <label className="flex items-center gap-2 text-sm font-medium">
        <input
          type="checkbox"
          checked={visibility === "public"}
          onChange={(event) => setVisibility(event.target.checked ? "public" : "private")}
        />
        Tornar nota pública (visível para outras pessoas)
      </label>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <Button type="submit" disabled={isPending || !content.trim()}>
        {isPending ? "Salvando..." : "Salvar nota"}
      </Button>
    </form>
  );
}
