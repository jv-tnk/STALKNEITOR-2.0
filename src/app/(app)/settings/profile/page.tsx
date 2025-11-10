import { redirect } from "next/navigation";
import { eq } from "drizzle-orm";

import { requireUser } from "@/lib/auth";
import { getDb, users } from "@/lib/db";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { HandleForm } from "@/components/settings/handle-form";

export default async function ProfileSettingsPage() {
  const user = await requireUser().catch(() => null);
  if (!user) redirect("/login");

  const db = getDb();
  const record = await db.query.users.findFirst({ where: eq(users.id, user.id) });

  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm uppercase tracking-wider text-muted-foreground">Perfil</p>
        <h1 className="text-3xl font-semibold">Configurações</h1>
        <p className="text-muted-foreground">
          Atualize seu handle público e dados básicos.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Identidade</CardTitle>
          <CardDescription>Seu handle é usado para rankings e notas públicas.</CardDescription>
        </CardHeader>
        <CardContent>
          <HandleForm currentHandle={record?.handle ?? ""} />
        </CardContent>
      </Card>
    </div>
  );
}
