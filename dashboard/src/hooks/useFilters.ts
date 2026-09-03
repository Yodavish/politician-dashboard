import { useSearchParams } from "react-router-dom";
import { useCallback, useMemo } from "react";

/**
 * Read/write URL search params as a typed filter object.
 * Keys and their default (sentinels) come from the `defaults` argument,
 * which also carries the type/kind of each value (number vs string).
 * Unknown params are preserved so other code can add them.
 */
export function useFilters<T extends object>(
  defaults: T,
): [T, (patch: Partial<T>) => void] {
  const [searchParams, setSearchParams] = useSearchParams();

  // Memoize on a stable signature of the filter values (not the object
  // identity or the searchParams object, whose reference can change every
  // render). This keeps the returned `filters` reference stable across
  // renders unless the URL actually changes, so effects depending on
  // `[filters]` do not re-run in a loop.
  const signature = useMemo(
    () =>
      Object.keys(defaults)
        .map((key) => `${key}=${searchParams.get(key) ?? ""}`)
        .join("&"),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [searchParams],
  );

  const filters = useMemo<T>(() => {
    const result = { ...defaults } as T;
    for (const key of Object.keys(defaults)) {
      const raw = searchParams.get(key);
      if (raw !== null) {
        const defaultValue = (defaults as Record<string, unknown>)[key];
        if (typeof defaultValue === "number") {
          const n = Number(raw);
          (result as Record<string, unknown>)[key] = isNaN(n)
            ? defaultValue
            : n;
        } else {
          (result as Record<string, unknown>)[key] = raw;
        }
      }
    }
    return result;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  const setFilters = useCallback(
    (patch: Partial<T>) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        for (const [k, v] of Object.entries(patch)) {
          if (v === undefined || v === null || v === "") {
            next.delete(k);
          } else {
            next.set(k, String(v));
          }
        }
        return next;
      });
    },
    [setSearchParams],
  );

  return [filters, setFilters];
}
