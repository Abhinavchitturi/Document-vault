"use client";

import { useEffect, useState } from "react";
import Icon from "./Icon";

export type Theme = "system" | "light" | "dark";
const THEMES: Theme[] = ["system", "light", "dark"];
const ICONS = { system: "monitor", light: "sun", dark: "moon" } as const;

export function readTheme(): Theme {
  try {
    const t = localStorage.getItem("theme") as Theme | null;
    return t && THEMES.includes(t) ? t : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(theme: Theme) {
  if (theme === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem("theme", theme);
  } catch {
    /* private mode */
  }
  window.dispatchEvent(new CustomEvent("vault-theme", { detail: theme }));
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>("system");
  useEffect(() => {
    setTheme(readTheme());
    const onChange = (e: Event) => setTheme((e as CustomEvent<Theme>).detail);
    window.addEventListener("vault-theme", onChange);
    return () => window.removeEventListener("vault-theme", onChange);
  }, []);
  return [theme, applyTheme];
}

export default function ThemeToggle() {
  const [theme, set] = useTheme();
  return (
    <button className="icon-btn" type="button" title={`Theme: ${theme}`} aria-label={`Theme: ${theme}. Change theme`}
            onClick={() => set(THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length])}>
      <Icon name={ICONS[theme]} />
    </button>
  );
}
