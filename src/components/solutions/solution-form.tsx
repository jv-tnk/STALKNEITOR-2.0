"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { CODE_LANGUAGES } from "@/lib/constants/languages";

export function SolutionForm({ guideModuleId, problemId }: { guideModuleId: string; problemId: string }) {
  const router = useRouter();
  const [content, setContent] = useState("");
  const [isPublic, setIsPublic] = useState(true);
  const [language, setLanguage] = useState("auto");
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    startTransition(async () => {
      const res = await fetch("/api/solutions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ guideModuleId, problemId, content, language, isPublic }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body.error ?? "Não foi possível enviar a solução.");
        return;
      }
      setContent("");
      setIsPublic(true);
      setLanguage("auto");
      router.refresh();
    });
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <Textarea
        rows={5}
        placeholder="Compartilhe sua abordagem ou snippet de código"
        value={content}
        onChange={(event) => setContent(event.target.value)}
      />
      <label className="flex flex-col gap-1 text-sm font-medium">
        Linguagem
        <select
          value={language}
          onChange={(event) => setLanguage(event.target.value)}
          className="rounded border border-input bg-background px-2 py-1 text-sm"
        >
          {CODE_LANGUAGES.map((lang) => (
            <option key={lang.value} value={lang.value}>
              {lang.label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-2 text-sm font-medium">
        <input
          type="checkbox"
          checked={isPublic}
          onChange={(event) => setIsPublic(event.target.checked)}
        />
        Tornar pública (visível a todos)
      </label>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <Button type="submit" disabled={isPending || !content.trim()}>
        {isPending ? "Enviando..." : "Salvar solução"}
      </Button>
    </form>
  );
}
