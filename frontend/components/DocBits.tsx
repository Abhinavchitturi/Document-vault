import type { Category } from "@/lib/api";
import Icon from "./Icon";

export const catStyle = (cat: Category) => ({ color: `var(--c-${cat})`, background: `var(--c-${cat}-soft)` });

export function FileIcon({ category, size = 34 }: { category: Category; size?: number }) {
  return (
    <span className="file-icon" style={{ ...catStyle(category), width: size, height: size }}>
      <Icon name="file" size={Math.round(size * 0.45)} strokeWidth={2} />
    </span>
  );
}

export function CategoryBadge({ category }: { category: Category }) {
  return <span className="badge" style={catStyle(category)}>{category}</span>;
}

/** Long file names wrap at underscores instead of overflowing. */
export const displayName = (name: string) => name.replace(/_/g, "_​");
