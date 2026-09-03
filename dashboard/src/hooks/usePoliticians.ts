import { useEffect, useState } from "react";
import { fetchPoliticians } from "../api/client";

export interface PoliticianName {
  name: string;
  stateDistrict: string;
}

type Lookup = Record<string, PoliticianName>;

/** Fetch all politicians and return an id → name lookup. */
export function usePoliticianNameMap(): Lookup {
  const [map, setMap] = useState<Lookup>({});

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const { items } = await fetchPoliticians({ limit: 500 });
        if (cancelled) return;
        const m: Lookup = {};
        for (const p of items) {
          m[p.id] = { name: p.name, stateDistrict: p.state_district };
        }
        setMap(m);
      } catch {
        // silently leave the map empty; UI will show the id
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  return map;
}
