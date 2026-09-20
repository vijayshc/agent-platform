/** Lazy loader for the DataTables library.
 *
 *  DataTables is a ~125 KB chunk and must not be parsed during the initial
 *  chat-history load, so every consumer goes through this one memoised dynamic
 *  import instead of importing the library directly.
 */
import type { Api, Options } from "datatables.net";

type DataTableCtor = new (table: HTMLTableElement, options?: Options) => Api<unknown>;

let ctor: Promise<DataTableCtor> | null = null;

export function loadDataTable(): Promise<DataTableCtor> {
  if (!ctor) {
    ctor = import("datatables.net-dt").then((m) => m.default as DataTableCtor);
  }
  return ctor;
}
