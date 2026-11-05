"use client";

/** TanStack Table v8 + TanStack Virtual: dense, virtualised (10k+ rows), right-aligned numbers,
 * units in headers. No motion inside tables. */
import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type Row,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, ArrowUp } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/cn";

export type Col<T> = ColumnDef<T, any> & { meta?: { num?: boolean; width?: number } };

export function DataTable<T>({
  data,
  columns,
  height = 520,
  onRowClick,
  rowKey,
  dense = true,
  sortable = true,
  renderSubRows,
  empty,
  className,
}: {
  data: T[];
  columns: Col<T>[];
  height?: number | "auto";
  onRowClick?: (r: T) => void;
  rowKey?: (r: T, i: number) => string;
  dense?: boolean;
  sortable?: boolean;
  renderSubRows?: (r: T) => React.ReactNode | null;
  empty?: React.ReactNode;
  className?: string;
}) {
  const [sorting, setSorting] = React.useState<SortingState>([]);
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: sortable ? getSortedRowModel() : undefined,
    getRowId: rowKey ? (r, i) => rowKey(r, i) : undefined,
  });
  const rows = table.getRowModel().rows;
  const parent = React.useRef<HTMLDivElement>(null);
  const virtual = height !== "auto" && !renderSubRows && rows.length > 200;
  const v = useVirtualizer({ count: virtual ? rows.length : 0, getScrollElement: () => parent.current, estimateSize: () => (dense ? 30 : 38), overscan: 20 });
  const cell = dense ? "px-2.5 py-1.5" : "px-3 py-2.5";

  const renderRow = (r: Row<T>, style?: React.CSSProperties) => (
    <React.Fragment key={r.id}>
      <tr
        style={style}
        onClick={onRowClick ? () => onRowClick(r.original) : undefined}
        className={cn("border-b border-line/70 hover:bg-line/30", onRowClick && "cursor-pointer")}
        data-testid="table-row"
      >
        {r.getVisibleCells().map((c) => (
          <td
            key={c.id}
            className={cn(cell, "whitespace-nowrap text-xs text-ink", (c.column.columnDef as Col<T>).meta?.num && "num text-right")}
            style={{ width: (c.column.columnDef as Col<T>).meta?.width }}
          >
            {flexRender(c.column.columnDef.cell, c.getContext())}
          </td>
        ))}
      </tr>
      {renderSubRows?.(r.original)}
    </React.Fragment>
  );

  return (
    <div ref={parent} className={cn("scroll-thin relative overflow-auto rounded-lg border border-line bg-surface", className)} style={{ maxHeight: height === "auto" ? undefined : height }}>
      <table className="w-full border-collapse">
        <thead className="sticky top-0 z-10 bg-surface">
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id} className="border-b border-line">
              {hg.headers.map((h) => {
                const meta = (h.column.columnDef as Col<T>).meta;
                const sorted = h.column.getIsSorted();
                return (
                  <th
                    key={h.id}
                    onClick={sortable && h.column.getCanSort() ? h.column.getToggleSortingHandler() : undefined}
                    className={cn(cell, "select-none whitespace-nowrap text-[11px] font-medium uppercase tracking-wide text-ink-3", meta?.num ? "text-right" : "text-left", sortable && h.column.getCanSort() && "cursor-pointer hover:text-ink-2")}
                    style={{ width: meta?.width }}
                  >
                    <span className="inline-flex items-center gap-1">
                      {flexRender(h.column.columnDef.header, h.getContext())}
                      {sorted === "asc" && <ArrowUp className="h-3 w-3" />}
                      {sorted === "desc" && <ArrowDown className="h-3 w-3" />}
                    </span>
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="px-3 py-8 text-center text-xs text-ink-3">
                {empty ?? "No rows"}
              </td>
            </tr>
          )}
          {virtual ? (
            <>
              {v.getVirtualItems().length > 0 && <tr style={{ height: v.getVirtualItems()[0].start }} />}
              {v.getVirtualItems().map((vi) => renderRow(rows[vi.index]))}
              {v.getVirtualItems().length > 0 && <tr style={{ height: v.getTotalSize() - v.getVirtualItems()[v.getVirtualItems().length - 1].end }} />}
            </>
          ) : (
            rows.map((r) => renderRow(r))
          )}
        </tbody>
      </table>
    </div>
  );
}
