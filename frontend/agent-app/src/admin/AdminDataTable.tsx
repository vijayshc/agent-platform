import { useEffect, useMemo, useState } from "react";

export interface Column<T> {
  key: string;
  header: string;
  render?: (row: T) => React.ReactNode;
  sortValue?: (row: T) => string | number;
  width?: string;
  className?: string;
}

interface AdminDataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  searchText?: (row: T) => string;
  searchPlaceholder?: string;
  pageSizeOptions?: number[];
  defaultPageSize?: number;
  emptyMessage?: string;
  onRowClick?: (row: T) => void;
  rowClassName?: (row: T) => string | undefined;
  tableClassName?: string;
  initialSortKey?: string;
  initialSortDir?: "asc" | "desc";
  compact?: boolean;
}

export function AdminDataTable<T>({
  columns,
  rows,
  rowKey,
  searchText,
  searchPlaceholder = "Search…",
  pageSizeOptions = [10, 25, 50, 100],
  defaultPageSize = 10,
  emptyMessage = "No records found.",
  onRowClick,
  rowClassName,
  tableClassName,
  initialSortKey,
  initialSortDir = "asc",
  compact,
}: AdminDataTableProps<T>) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(defaultPageSize);
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<string | undefined>(initialSortKey);
  const [sortDir, setSortDir] = useState<"asc" | "desc">(initialSortDir);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q || !searchText) return rows;
    return rows.filter((r) => searchText(r).toLowerCase().includes(q));
  }, [rows, query, searchText]);

  const sorted = useMemo(() => {
    if (!sortKey) return filtered;
    const col = columns.find((c) => c.key === sortKey);
    if (!col) return filtered;
    const val = col.sortValue || ((r: T) => String((r as Record<string, unknown>)[sortKey] ?? ""));
    const list = [...filtered];
    list.sort((a, b) => {
      const va = val(a);
      const vb = val(b);
      if (va < vb) return sortDir === "asc" ? -1 : 1;
      if (va > vb) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
    return list;
  }, [filtered, sortKey, sortDir, columns]);

  useEffect(() => setPage(1), [query, pageSize, rows]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const paged = sorted.slice((safePage - 1) * pageSize, safePage * pageSize);

  const handleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  return (
    <div className={`aa-admin-dt${compact ? " compact" : ""}`}>
      <div className="aa-dt-toolbar aa-admin-dt-toolbar">
        <div className="aa-dt-length">
          <span>Show</span>
          <select
            id="aa-dt-page-size"
            name="aa-dt-page-size"
            value={pageSize}
            onChange={(e) => setPageSize(Number(e.target.value))}
          >
            {pageSizeOptions.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
          <span>entries</span>
        </div>
        {searchText && (
          <div className="aa-dt-filter">
            <span>Search:</span>
            <input
              id="aa-dt-search"
              name="aa-dt-search"
              className="aa-search aa-dt-search"
              placeholder={searchPlaceholder}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
        )}
      </div>

      <div className="aa-admin-dt-wrap">
        <table className={["aa-table", "aa-dt", "no-datatable", tableClassName || ""].join(" ").trim()}>
          <thead>
            <tr>
              {columns.map((c) => (
                <th
                  key={c.key}
                  style={c.width ? { width: c.width } : undefined}
                  className={[
                    c.className || "",
                    sortKey === c.key ? `sorting_${sortDir}` : (c.sortValue ? "sorting" : ""),
                  ].join(" ")}
                  aria-sort={
                    !c.sortValue
                      ? undefined
                      : sortKey === c.key
                        ? sortDir === "asc"
                          ? "ascending"
                          : "descending"
                        : "none"
                  }
                  onClick={c.sortValue ? () => handleSort(c.key) : undefined}
                >
                  {c.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {paged.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="aa-table-empty">
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              paged.map((row) => (
                <tr
                  key={rowKey(row)}
                  className={[
                    onRowClick ? "aa-admin-dt-row-clickable" : "",
                    rowClassName ? rowClassName(row) || "" : "",
                  ].join(" ").trim() || undefined}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                >
                  {columns.map((c) => (
                    <td key={c.key} className={c.className}>
                      {c.render ? c.render(row) : String((row as Record<string, unknown>)[c.key] ?? "")}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {sorted.length > 0 && (
        <div className="aa-pagination aa-admin-dt-pagination">
          <span className="aa-page-count">
            Showing {(safePage - 1) * pageSize + 1}–{Math.min(safePage * pageSize, sorted.length)} of {sorted.length}
          </span>
          <button type="button" className="aa-btn aa-page-btn" disabled={safePage <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
            ‹ Prev
          </button>
          <span className="aa-page-info">
            Page {safePage} of {totalPages}
          </span>
          <button type="button" className="aa-btn aa-page-btn" disabled={safePage >= totalPages} onClick={() => setPage((p) => p + 1)}>
            Next ›
          </button>
        </div>
      )}
    </div>
  );
}
