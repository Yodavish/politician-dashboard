import { useEffect, useState } from "react";
import { fetchHealth } from "@/api/client";
import { Badge } from "@/components/ui/badge";

export default function HealthBadge() {
  const [status, setStatus] = useState<"ok" | "error" | "loading">("loading");

  useEffect(() => {
    let cancelled = false;
    async function check() {
      try {
        const h = await fetchHealth();
        if (!cancelled) setStatus(h.status === "ok" ? "ok" : "error");
      } catch {
        if (!cancelled) setStatus("error");
      }
    }
    check();
    const id = setInterval(check, 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (status === "loading") {
    return (
      <Badge variant="outline" className="gap-1.5">
        <span className="bg-muted-foreground/60 size-2 animate-pulse rounded-full" />
        API
      </Badge>
    );
  }

  const ok = status === "ok";
  return (
    <Badge
      variant="outline"
      className={
        ok
          ? "gap-1.5 border-emerald-500/40 bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400"
          : "gap-1.5 border-red-500/40 bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-400"
      }
    >
      <span
        className={
          ok
            ? "bg-emerald-500 size-2 rounded-full"
            : "bg-red-500 size-2 rounded-full"
        }
      />
      {ok ? "API" : "API down"}
    </Badge>
  );
}
