import { SiteHeader } from "@/components/layout/site-header";

type AppShellProps = {
  children: React.ReactNode;
  user: {
    id: string;
    email: string;
    name?: string | null;
    handle?: string | null;
  };
};

export function AppShell({ children, user }: AppShellProps) {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <SiteHeader user={user} />
      <main className="mx-auto w-full max-w-6xl px-4 py-8">{children}</main>
    </div>
  );
}
