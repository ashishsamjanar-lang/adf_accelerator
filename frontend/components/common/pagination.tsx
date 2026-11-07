"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";

interface PaginationProps {
  page: number;
  totalPages: number;
  totalItems: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  /** Plural noun for the summary text, e.g. "pipelines". Defaults to "items". */
  itemLabel?: string;
  className?: string;
}

/** Prev/Next pager with a "Showing X–Y of Z" summary. Renders nothing for a single page. */
export function Pagination({
  page,
  totalPages,
  totalItems,
  pageSize,
  onPageChange,
  itemLabel = "items",
  className,
}: PaginationProps) {
  if (totalItems === 0 || totalPages <= 1) return null;
  const start = (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, totalItems);

  return (
    <div className={`pagination-bar${className ? ` ${className}` : ""}`}>
      <span className="pagination-summary">
        Showing {start}–{end} of {totalItems} {itemLabel}
      </span>
      <div className="pagination-controls">
        <Button
          variant="outline"
          size="sm"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft className="h-3.5 w-3.5" />
          Prev
        </Button>
        <span className="pagination-page">
          Page {page} of {totalPages}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
        >
          Next
          <ChevronRight className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}
