import React from "react";

export interface DataTableProps {
  children?: React.ReactNode;
  className?: string;
  wrapperClassName?: string;
  testId?: string;
  tableInit?: string;
}

export function DataTable({
  children,
  className = "table table-hover table-bordered chat-datatable-table",
  wrapperClassName = "table-responsive my-2",
  testId,
  tableInit,
}: DataTableProps) {
  return (
    <div className={wrapperClassName}>
      <table className={className} data-testid={testId} data-table-init={tableInit}>
        {children}
      </table>
    </div>
  );
}
