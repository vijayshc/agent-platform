import { useEffect, useRef } from "react";
import type { Api, Options } from "datatables.net";
import "datatables.net-dt/css/dataTables.dataTables.css";
import { loadDataTable } from "./dataTables";
import type { TableSpec, ToolDataPayload } from "./toolDataTypes";

function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value));
}

/**
 * The full cached result as an interactive DataTable.
 *
 * Rows are handed to DataTables directly (not rendered by React) so a result
 * with thousands of rows costs a few nodes instead of tens of thousands; with
 * `deferRender` DataTables only builds the cells it displays.
 */
export function ToolDataTable({ data, spec }: { data: ToolDataPayload; spec: TableSpec }) {
  const tableRef = useRef<HTMLTableElement>(null);
  const dtRef = useRef<Api<unknown> | null>(null);
  const pageLength = clamp(Math.floor(Number(spec.pageLength) || 25), 5, 200);

  useEffect(() => {
    let cancelled = false;
    if (!tableRef.current) return;
    loadDataTable()
      .then((Ctor) => {
        if (cancelled || !tableRef.current || dtRef.current) return;
        const options: Options = {
          data: data.rows,
          columns: data.columns.map((column) => ({ title: column })),
          autoWidth: false,
          deferRender: true,
          order: [],
          pageLength,
          lengthMenu: [10, 25, 50, 100],
          // No scrollX: DataTables clones the header for horizontal scrolling,
          // which duplicates every header cell in the accessibility tree. A wide
          // table scrolls inside .td-table-wrap instead.
          scrollX: false,
          searchDelay: 250,
          language: {
            search: "",
            searchPlaceholder: "Search rows…",
            lengthMenu: "_MENU_ per page",
            info: "_START_–_END_ of _TOTAL_ rows",
            infoEmpty: "No rows",
            zeroRecords: "No matching rows",
            paginate: { previous: "‹", next: "›" },
          },
        };
        dtRef.current = new Ctor(tableRef.current, options);
      })
      .catch(() => {
        // The DataTables chunk failed to load; the card still reports the row count.
      });
    return () => {
      cancelled = true;
      dtRef.current?.destroy();
      dtRef.current = null;
    };
  }, [data, pageLength]);

  return (
    <figure className="td-card td-card-table" data-testid={`table-card-${data.call_id}`}>
      <figcaption className="td-card-head">
        <div className="td-card-titles">
          <span className="td-card-title">{spec.title || `Table · ${data.tool_name}`}</span>
        </div>
        <span className="td-card-meta" title="Rows in this result">
          {data.total_rows.toLocaleString()} row{data.total_rows === 1 ? "" : "s"}
          {data.truncated ? ` · showing ${data.returned_rows.toLocaleString()}` : ""}
        </span>
      </figcaption>
      <div className="td-table-wrap">
        <table ref={tableRef} className="td-table" />
      </div>
      {data.truncated ? (
        <div className="td-card-foot">
          Showing {data.returned_rows.toLocaleString()} of {data.total_rows.toLocaleString()} rows (cache limit).
        </div>
      ) : null}
    </figure>
  );
}
