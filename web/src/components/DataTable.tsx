import {
  ColumnDef, flexRender, getCoreRowModel, getSortedRowModel, SortingState, useReactTable,
} from '@tanstack/react-table'
import { useState } from 'react'

export type Col<T> = ColumnDef<T, any> & { left?: boolean; wrap?: boolean; tip?: string }

export default function DataTable<T>({
  data, columns, initialSort, maxRows,
}: { data: T[]; columns: Col<T>[]; initialSort?: SortingState; maxRows?: number }) {
  const [sorting, setSorting] = useState<SortingState>(initialSort ?? [])
  const table = useReactTable({
    data, columns, state: { sorting }, onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
  })
  const rows = maxRows ? table.getRowModel().rows.slice(0, maxRows) : table.getRowModel().rows
  return (
    <div className="tablewrap">
      <table className="grid">
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => {
                const def = h.column.columnDef as Col<T>
                return (
                  <th key={h.id} className={`${def.left ? 'l' : ''} ${def.tip ? 'tip' : ''}`} title={def.tip} onClick={h.column.getToggleSortingHandler()}>
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    {{ asc: ' ▲', desc: ' ▼' }[h.column.getIsSorted() as string] ?? ''}
                  </th>
                )
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              {r.getVisibleCells().map((c) => {
                const def = c.column.columnDef as Col<T>
                return (
                  <td key={c.id} className={`${def.left ? 'l' : ''} ${def.wrap ? 'wrap' : ''}`}>
                    {flexRender(c.column.columnDef.cell, c.getContext())}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {maxRows && table.getRowModel().rows.length > maxRows && (
        <div className="muted small" style={{ padding: 6 }}>
          showing {maxRows} of {table.getRowModel().rows.length} rows
        </div>
      )}
    </div>
  )
}
