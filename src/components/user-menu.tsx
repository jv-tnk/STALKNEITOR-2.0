"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { createSupabaseBrowserClient } from "@/lib/supabase/client";

interface UserMenuProps {
  name?: string | null;
  email?: string | null;
  handle?: string | null;
}

export function UserMenu({ name, email, handle }: UserMenuProps) {
  const router = useRouter();
  const supabase = createSupabaseBrowserClient();
  const [loading, setLoading] = useState(false);

  const handleSignOut = async () => {
    setLoading(true);
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  };

  return (
    <div className="flex items-center gap-3 text-sm">
      <div className="text-right">
        <p className="font-semibold leading-tight">{name ?? email}</p>
        <p className="text-xs text-muted-foreground">{handle ? `@${handle}` : email}</p>
      </div>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="ghost" asChild>
          <Link href="/settings/profile">Perfil</Link>
        </Button>
        <Button size="sm" variant="outline" onClick={handleSignOut} disabled={loading}>
          Sair
        </Button>
      </div>
    </div>
  );
}
