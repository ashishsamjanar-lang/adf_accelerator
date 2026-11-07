"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import type { CatalogArtifactType } from "@/types";

export interface CatalogNames {
  /** Artifact names of `type` on the project's base branch, as last indexed from Git. */
  names: string[];
  /** False until the base branch has been indexed at least once (Catalog → Git repository → Refresh). */
  indexed: boolean;
  branch: string | null;
}

const EMPTY: CatalogNames = { names: [], indexed: false, branch: null };

/**
 * §24.6/§24.8 pick-or-reference: names that already exist in the project's repo, for "use existing"
 * suggestions and "is it really there?" checks. Degrades to empty on any failure — a suggestion list
 * must never block the form.
 */
export function useCatalogNames(projectId: string | undefined, type: CatalogArtifactType): CatalogNames {
  const [state, setState] = useState<CatalogNames>(EMPTY);
  useEffect(() => {
    if (!projectId) {
      setState(EMPTY);
      return;
    }
    let cancelled = false;
    api.catalog
      .list(projectId, { type })
      .then((listing) => {
        if (!cancelled) setState({ names: listing.entries.map((e) => e.name), indexed: Boolean(listing.indexedAt), branch: listing.branch });
      })
      .catch(() => {
        if (!cancelled) setState(EMPTY);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, type]);
  return state;
}
