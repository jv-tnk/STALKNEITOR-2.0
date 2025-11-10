"use client";

import { useState, useTransition } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function HandleForm({ currentHandle }: { currentHandle?: string }) {
  const [handle, setHandle] = useState(currentHandle ?? "");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const onSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setMessage(null);
    setError(null);
    startTransition(async () => {
      const res = await fetch("/api/profile/handle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ handle }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body.error ?? "Não foi possível atualizar o handle");
        return;
      }
      const body = await res.json();
      setHandle(body.handle ?? handle);
      setMessage("Handle atualizado!");
    });
  };

  return (
    <form onSubmit={onSubmit} className="space-y-3">
      <label className="flex flex-col gap-1 text-sm font-medium">
        Handle
        <Input
          value={handle}
          onChange={(event) => setHandle(event.target.value)}
          placeholder="ex.: fulano123"
          pattern="^[a-zA-Z0-9_]{3,24}$"
          required
        />
      </label>
      <p className="text-xs text-muted-foreground">
        3-24 caracteres, apenas letras, números ou underscore. Esse nome aparece em rankings e notas públicas.
      </p>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {message && <p className="text-sm text-green-600">{message}</p>}
      <Button type="submit" disabled={isPending}>
        {isPending ? "Salvando..." : "Atualizar handle"}
      </Button>
    </form>
  );
}
