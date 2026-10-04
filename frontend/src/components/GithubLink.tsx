import type { ReactNode } from "react";
import { safeGithubUrl } from "../format";

export function GithubLink({
  url, children, fallback = children,
}: {
  url: string;
  children: ReactNode;
  fallback?: ReactNode;
}) {
  return safeGithubUrl(url) ? (
    <a href={url} target="_blank" rel="noopener noreferrer">{children} ↗</a>
  ) : <span>{fallback}</span>;
}
