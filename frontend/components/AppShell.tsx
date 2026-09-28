"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type Info } from "@/lib/api";
import { endSession, getSession } from "@/lib/session";
import Icon, { type IconName } from "./Icon";
import ThemeToggle from "./ThemeToggle";

const NAV: { href: string; label: string; icon: IconName }[] = [
  { href: "/", label: "Dashboard", icon: "home" },
  { href: "/chat/", label: "Chat", icon: "chat" },
  { href: "/documents/", label: "Documents", icon: "docs" },
  { href: "/upload/", label: "Upload", icon: "upload" },
  { href: "/settings/", label: "Settings", icon: "settings" },
];

interface Vault {
  info: Info | null;
  error: string | null;
  refresh: () => Promise<void>;
}
const VaultContext = createContext<Vault>({ info: null, error: null, refresh: async () => {} });
export const useVault = () => useContext(VaultContext);

function StatusPill({ info }: { info: Info | null }) {
  if (!info) return <span className="pill"><span className="dot" />Checking…</span>;
  const { ready, message } = info.llm_status;
  return (
    <span className={`pill ${ready ? "ready" : "not-ready"}`} title={message}>
      <span className="dot" />
      {ready ? "Assistant ready" : "Assistant offline"}
    </span>
  );
}

export default function AppShell({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [info, setInfo] = useState<Info | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [signedIn, setSignedIn] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setInfo(await api.info());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    if (!getSession()) {
      router.replace(`/login/?next=${encodeURIComponent(pathname)}`);
      return;
    }
    setSignedIn(true);
    refresh();
  }, [pathname, refresh, router]);

  if (!signedIn) return null;
  const owner = info?.owner ?? getSession() ?? "";
  const current = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href)) ? "page" : undefined;

  return (
    <VaultContext.Provider value={{ info, error, refresh }}>
      <div className="shell">
        <nav className="sidebar" aria-label="Main">
          <div className="brand"><span className="logo"><Icon name="file" size={16} strokeWidth={2.4} /></span>Document Vault</div>
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className="nav-link" aria-current={current(n.href)}>
              <Icon name={n.icon} size={17} />{n.label}
            </Link>
          ))}
          <div className="spacer" />
          <div className="profile">
            <span className="avatar-initial">{owner.slice(0, 1)}</span>
            <div className="who"><strong>{owner}</strong><span><Icon name="lock" size={11} /> On this computer</span></div>
            <button className="icon-btn" type="button" title="Sign out" aria-label="Sign out"
                    onClick={() => { endSession(); router.replace("/login/"); }}>
              <Icon name="logout" />
            </button>
          </div>
        </nav>
        <div className="main">
          <header className="topbar">
            <h1>{title}</h1>
            {actions}
            <StatusPill info={info} />
            <ThemeToggle />
          </header>
          {error && (
            <div className="page"><div className="notice bad"><Icon name="warn" />Can&apos;t reach the vault server: {error}</div></div>
          )}
          {children}
        </div>
        <nav className="tabbar" aria-label="Main">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} aria-current={current(n.href)}>
              <Icon name={n.icon} size={20} />{n.label}
            </Link>
          ))}
        </nav>
      </div>
    </VaultContext.Provider>
  );
}
