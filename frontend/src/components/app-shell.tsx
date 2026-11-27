"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Building2, FilePlus2, FolderKanban, LayoutDashboard, LogOut, Settings, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Badge, LoadingBlock } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { date, label } from "@/lib/format";
import { useLogout, useMe, useSessionGuard } from "@/lib/session";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/borrowers", label: "Borrowers", icon: Building2 },
  { href: "/assessments", label: "Assessments", icon: FolderKanban },
  { href: "/assessments/new", label: "New assessment", icon: FilePlus2, perm: "assessments:write" },
  { href: "/settings", label: "Settings & admin", icon: Settings },
];

interface Notif { id: number; title: string; message: string; level: string; link: string | null; is_read: boolean; created_at: string }

function Notifications() {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const router = useRouter();
  const { data } = useQuery({ queryKey: ["notifications"], queryFn: () => api.get<{ items: Notif[]; unread: number }>("/notifications"), refetchInterval: 30_000 });
  const readAll = useMutation({ mutationFn: () => api.post("/notifications/read-all"), onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }) });
  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} className="relative rounded-md p-2 text-muted-foreground hover:bg-muted" aria-label={`Notifications (${data?.unread ?? 0} unread)`}>
        <Bell className="size-5" />
        {!!data?.unread && <span className="absolute right-1 top-1 grid min-w-4 place-items-center rounded-full bg-red-600 px-1 text-[10px] font-semibold text-white">{data.unread}</span>}
      </button>
      {open && (
        <div className="absolute right-0 z-30 mt-2 w-96 rounded-lg border bg-card shadow-lg">
          <div className="flex items-center justify-between border-b px-4 py-2.5">
            <span className="text-sm font-semibold">Notifications</span>
            <button className="text-xs text-primary hover:underline" onClick={() => readAll.mutate()}>Mark all read</button>
          </div>
          <ul className="max-h-96 overflow-y-auto">
            {!data?.items.length && <li className="px-4 py-6 text-center text-sm text-muted-foreground">No notifications yet.</li>}
            {data?.items.map((n) => (
              <li key={n.id}>
                <button className={cn("w-full px-4 py-3 text-left hover:bg-muted", !n.is_read && "bg-sky-50/60")}
                  onClick={() => { setOpen(false); if (n.link) router.push(n.link); }}>
                  <div className="text-sm font-medium">{n.title}</div>
                  <div className="text-xs text-muted-foreground">{n.message}</div>
                  <div className="mt-1 text-[11px] text-muted-foreground">{date(n.created_at, true)}</div>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  useSessionGuard();
  const { data: me, isLoading, error } = useMe();
  const pathname = usePathname();
  const router = useRouter();
  const logout = useLogout();

  useEffect(() => {
    if (error && error.status === 401) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [error, router, pathname]);

  if (isLoading || !me) return <div className="min-h-screen"><LoadingBlock label="Checking your session…" /></div>;

  const active = (href: string) => (href === "/" ? pathname === "/" : pathname === href || (pathname.startsWith(href + "/") && !(href === "/assessments" && pathname === "/assessments/new")));

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col bg-sidebar text-sidebar-foreground md:flex">
        <div className="flex items-center gap-2 px-5 py-5">
          <div className="grid size-8 place-items-center rounded-md bg-sky-500/20 text-sky-300"><ShieldCheck className="size-5" /></div>
          <div><div className="text-sm font-semibold text-white">Intelli-Credit</div><div className="text-[11px] text-sidebar-muted">MSME credit intelligence</div></div>
        </div>
        <nav className="flex-1 space-y-0.5 px-3" aria-label="Main">
          {NAV.filter((n) => !n.perm || me.permissions.includes(n.perm)).map(({ href, label: l, icon: Icon }) => (
            <Link key={href} href={href} className={cn("flex items-center gap-3 rounded-md px-3 py-2 text-sm text-sidebar-muted hover:bg-white/5 hover:text-white", active(href) && "bg-white/10 font-medium text-white")}>
              <Icon className="size-4" />{l}
            </Link>
          ))}
        </nav>
        <div className="border-t border-white/10 px-5 py-4 text-[11px] leading-relaxed text-sidebar-muted">
          Decision-support tool. Recommendations require review by an authorised credit officer.
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center justify-between border-b bg-card/95 px-6 backdrop-blur">
          <div className="flex items-center gap-2 text-sm">
            <span className="font-medium">{me.organization.name}</span>
            {me.organization.is_demo && <Badge tone="warning">Demo · synthetic data</Badge>}
          </div>
          <div className="flex items-center gap-3">
            <Notifications />
            <div className="text-right leading-tight">
              <div className="text-sm font-medium">{me.user.full_name}</div>
              <div className="text-xs text-muted-foreground">{label(me.role)}</div>
            </div>
            <button onClick={logout} className="rounded-md p-2 text-muted-foreground hover:bg-muted" aria-label="Sign out" title="Sign out"><LogOut className="size-4" /></button>
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-6 py-6">{children}</main>
      </div>
    </div>
  );
}
