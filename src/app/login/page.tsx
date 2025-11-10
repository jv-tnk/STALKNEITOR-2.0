import { redirect } from "next/navigation";

import { LoginForm } from "@/components/auth/login-form";
import { getCurrentUser } from "@/lib/auth";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ message?: string; email?: string }>;
}) {
  const params = await searchParams;
  const user = await getCurrentUser();

  if (user) {
    redirect("/dashboard");
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-muted/20 px-4 py-16">
      <div className="mb-8 text-center">
        <p className="text-sm uppercase tracking-wider text-muted-foreground">
          Supabase Auth
        </p>
        <h1 className="text-3xl font-semibold">Entrar no Stalkneitor 2.0</h1>
        <p className="text-muted-foreground">
          Enviaremos um link mágico para o seu e-mail.
        </p>
      </div>
      <LoginForm message={params.message} defaultEmail={params.email} />
    </div>
  );
}
