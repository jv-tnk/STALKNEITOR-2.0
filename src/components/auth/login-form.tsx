"use client";

import { startTransition, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { createSupabaseBrowserClient } from "@/lib/supabase/client";

type Mode = "magic" | "password";
type AuthType = "login" | "signup";

export function LoginForm({
  defaultEmail,
  message,
}: {
  defaultEmail?: string;
  message?: string;
}) {
  const [mode, setMode] = useState<Mode>("password");
  const [authType, setAuthType] = useState<AuthType>("login");
  const [email, setEmail] = useState(defaultEmail ?? "");
  const [handleInput, setHandleInput] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "sent" | "error">(
    "idle",
  );
  const [error, setError] = useState<string | null>(null);
  const [lastSentEmail, setLastSentEmail] = useState<string | null>(null);
  const router = useRouter();

  useEffect(() => {
    if (typeof window === "undefined") return;
    const saved = window.localStorage.getItem("stalkneitor:last-email");
    if (saved) {
      startTransition(() => {
        setEmail((current) => current || saved);
        setLastSentEmail(saved);
      });
    }
  }, []);

  const supabase = createSupabaseBrowserClient();

  const handleMagicLink = async () => {
    setStatus("loading");
    setError(null);
    try {
      const redirectTo = `${window.location.origin}/auth/callback`;
      const { error: signInError } = await supabase.auth.signInWithOtp({
        email,
        options: {
          emailRedirectTo: redirectTo,
          shouldCreateUser: true,
        },
      });
      if (signInError) {
        setError(signInError.message);
        setStatus("error");
        return;
      }
      window.localStorage.setItem("stalkneitor:last-email", email);
      setLastSentEmail(email);
      setStatus("sent");
    } catch (err) {
      console.error(err);
      setError("Não foi possível enviar o link. Tente novamente.");
      setStatus("error");
    }
  };

  const handlePasswordAuth = async () => {
    setStatus("loading");
    setError(null);
    try {
      if (authType === "signup") {
        const redirectTo = `${window.location.origin}/auth/callback`;
        const { error: signUpError } = await supabase.auth.signUp({
          email,
          password,
          options: {
            emailRedirectTo: redirectTo,
            data: { handle: handleInput.trim() || undefined },
          },
        });
        if (signUpError) {
          setError(signUpError.message);
          setStatus("error");
          return;
        }
        setStatus("sent");
        return;
      }

      const { error: loginError } = await supabase.auth.signInWithPassword({
        email,
        password,
      });
      if (loginError) {
        setError(loginError.message);
        setStatus("error");
        return;
      }
      router.refresh();
    } catch (err) {
      console.error(err);
      setError("Algo deu errado. Tente novamente.");
      setStatus("error");
    } finally {
      setStatus("idle");
    }
  };

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (mode === "magic") {
      void handleMagicLink();
    } else {
      void handlePasswordAuth();
    }
  };

  const handleResend = async () => {
    if (!lastSentEmail) return;
    setEmail(lastSentEmail);
    setStatus("loading");
    setError(null);
    try {
      const redirectTo = `${window.location.origin}/auth/callback`;
      const { error } = await supabase.auth.signInWithOtp({
        email: lastSentEmail,
        options: {
          emailRedirectTo: redirectTo,
        },
      });
      if (error) {
        setError(error.message);
        setStatus("error");
        return;
      }
      setStatus("sent");
    } catch (err) {
      console.error(err);
      setError("Não foi possível reenviar o link.");
      setStatus("error");
    }
  };

  return (
    <Card className="w-full max-w-md">
      <CardHeader>
        <CardTitle>{mode === "magic" ? "Login via link" : "Entrar com senha"}</CardTitle>
        <CardDescription>
          {mode === "magic"
            ? "Use um link rápido (ideal para primeiro acesso)."
            : authType === "signup"
            ? "Crie sua conta com e-mail e senha."
            : "Entre com seu e-mail/senha."}
        </CardDescription>
        <div className="flex gap-2">
          <Button
            type="button"
            variant={mode === "password" ? "default" : "outline"}
            size="sm"
            onClick={() => setMode("password")}
          >
            Email + senha
          </Button>
          <Button
            type="button"
            variant={mode === "magic" ? "default" : "outline"}
            size="sm"
            onClick={() => setMode("magic")}
          >
            Link mágico
          </Button>
        </div>
        {mode === "password" && (
          <div className="flex gap-2">
            <Button
              type="button"
              variant={authType === "login" ? "default" : "outline"}
              size="sm"
              onClick={() => setAuthType("login")}
            >
              Entrar
            </Button>
            <Button
              type="button"
              variant={authType === "signup" ? "default" : "outline"}
              size="sm"
              onClick={() => setAuthType("signup")}
            >
              Criar conta
            </Button>
          </div>
        )}
      </CardHeader>
      <CardContent>
        <form className="space-y-4" onSubmit={handleSubmit}>
          <label className="flex flex-col gap-1 text-sm font-medium">
            E-mail
            <Input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="voce@email.com"
              required
            />
          </label>
          {mode === "password" && authType === "signup" && (
            <div className="space-y-1 text-sm font-medium">
              <label className="flex flex-col gap-1">
                Handle (opcional)
                <Input
                  value={handleInput}
                  onChange={(event) => setHandleInput(event.target.value)}
                  placeholder="ex.: fulano123"
                  pattern="^[a-zA-Z0-9_]{3,24}$"
                />
              </label>
              <p className="text-xs font-normal text-muted-foreground">
                Use apenas letras, números ou underscore.
              </p>
            </div>
          )}
          {mode === "password" && (
            <label className="flex flex-col gap-1 text-sm font-medium">
              Senha
              <Input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="••••••••"
                required
              />
            </label>
          )}
          <Button type="submit" className="w-full" disabled={status === "loading"}>
            {status === "loading"
              ? "Processando..."
              : mode === "magic"
              ? "Enviar link"
              : authType === "signup"
              ? "Criar conta"
              : "Entrar"}
          </Button>
        </form>
        {message && status !== "sent" && (
          <p className="mt-4 text-sm text-muted-foreground">{message}</p>
        )}
        {status === "sent" && (
          <p className="mt-4 text-sm text-green-600">
            Verifique sua caixa de entrada para concluir o acesso.
          </p>
        )}
        {error && (
          <p className="mt-4 text-sm text-destructive">
            {error}
          </p>
        )}
      </CardContent>
      {mode === "magic" && (
        <CardFooter className="text-xs text-muted-foreground">
          <div className="flex flex-col gap-2">
            <span>
              Ao continuar você concorda em receber comunicações do Supabase para autenticação.
            </span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="justify-start px-0 text-xs text-primary"
              disabled={!lastSentEmail || status === "loading"}
              onClick={handleResend}
            >
              Reenviar link para {lastSentEmail ?? "seu e-mail"}
            </Button>
          </div>
        </CardFooter>
      )}
    </Card>
  );
}
