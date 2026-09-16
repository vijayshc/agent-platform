import { memo, useEffect, useRef, type ReactNode } from "react";
import type { Options, Api } from "datatables.net";
import "datatables.net-dt/css/dataTables.dataTables.css";

type DataTableCtor = new (table: HTMLTableElement, options?: Options) => Api<any>;

let dataTableCtor: Promise<DataTableCtor> | null = null;

/** Lazily load the DataTables library only when a table is actually rendered,
 *  so the (large) DataTables module is not parsed/executed during the initial
 *  chat-history load and does not block the main thread (which was causing the
 *  first `/api/v1/conversations` request to "stall"). */
function loadDataTable(): Promise<DataTableCtor> {
  if (!dataTableCtor) {
    dataTableCtor = import("datatables.net-dt").then((m) => m.default);
  }
  return dataTableCtor;
}

/**
 * Wraps a markdown-rendered <table> and upgrades it to an open-source
 * DataTables instance, giving the chat table a search box, column ordering,
 * a page-length selector and pagination (as the app's earlier DataTables
 * interceptor did).
 *
 * DataTables mutates the DOM it owns (wraps the table in a `.dt-container`,
 * moves the header into a sorting layer and re-orders rows), so we initialise
 * it on mount and destroy it on unmount. Markdown "frozen" blocks are atomic
 * and stable once rendered, so the table subtree React owns does not change
 * while a given instance is live.
 */
export const ChatDataTable = memo(function ChatDataTable({
  children,
}: {
  children: ReactNode;
}) {
  const tableRef = useRef<HTMLTableElement>(null);
  const dtRef = useRef<Api<any> | null>(null);
  const pendingRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    const table = tableRef.current;
    if (!table || pendingRef.current) return;
    pendingRef.current = true;

    loadDataTable()
      .then((Ctor) => {
        if (cancelled || !tableRef.current || dtRef.current) return;
        dtRef.current = new Ctor(tableRef.current, {
          autoWidth: false,
          order: [],
          pageLength: 10,
          lengthMenu: [5, 10, 25, 50],
        });
      })
      .catch(() => {
        // If the DataTables chunk fails to load, fall back to a plain table.
      })
      .finally(() => {
        pendingRef.current = false;
      });

    return () => {
      cancelled = true;
      dtRef.current?.destroy();
      dtRef.current = null;
    };
  }, []);

  return (
    <div className="chat-datatable-wrapper">
      <table ref={tableRef} className="chat-datatable-table">
        {children}
      </table>
    </div>
  );
});
