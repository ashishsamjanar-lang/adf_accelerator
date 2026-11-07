"use client";

import { useEffect, useState } from "react";

export const DEFAULT_PAGE_SIZE = 10;

interface UsePaginationOptions {
  /** Items per page. Defaults to 10. */
  pageSize?: number;
  /**
   * Any value that should reset the current page back to 1 when it changes —
   * pass the active filter/search state here so a new filter always starts
   * from page 1 instead of landing on a page that may now be empty.
   */
  resetKey?: unknown;
}

/**
 * Client-side pagination over an already-filtered array. Slices `items` into
 * a page of `pageSize` (default 10) and exposes prev/next-friendly state.
 * Clamps automatically if the item count shrinks below the current page
 * (e.g. a filter removed rows) and resets to page 1 when `resetKey` changes.
 */
export function usePagination<T>(items: T[], options: UsePaginationOptions = {}) {
  const { pageSize = DEFAULT_PAGE_SIZE, resetKey } = options;
  const [page, setPage] = useState(1);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => setPage(1), [resetKey]);

  const totalItems = items.length;
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const safePage = Math.min(Math.max(page, 1), totalPages);

  useEffect(() => {
    if (page !== safePage) setPage(safePage);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [safePage]);

  const start = (safePage - 1) * pageSize;
  const pageItems = items.slice(start, start + pageSize);

  return {
    page: safePage,
    setPage,
    totalPages,
    totalItems,
    pageSize,
    pageItems,
    start,
  };
}
