import { useMemo, useState } from 'react'

import type { ReportAnimal } from '@shared/types'

import { formatPercent } from '../lib/format'

type SortKey = 'id' | 'label' | 'confidence' | 'observations' | 'spread_m'

interface Props {
  animals: ReportAnimal[]
  selectedId: string | null
  onSelect: (id: string) => void
}

const COLUMNS: { key: SortKey; label: string; numeric?: boolean }[] = [
  { key: 'id', label: 'Animal' },
  { key: 'label', label: 'Class' },
  { key: 'confidence', label: 'Confidence', numeric: true },
  { key: 'observations', label: 'Sightings', numeric: true },
  { key: 'spread_m', label: 'Spread', numeric: true }
]

export function AnimalTable({ animals, selectedId, onSelect }: Props) {
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'id', dir: 1 })
  const [filter, setFilter] = useState('')

  const rows = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const list = q ? animals.filter((a) => a.id.toLowerCase().includes(q) || a.label.toLowerCase().includes(q)) : animals
    return [...list].sort((a, b) => {
      const x = a[sort.key]
      const y = b[sort.key]
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir
    })
  }, [animals, sort, filter])

  return (
    <div className="table-wrap">
      <div className="table-toolbar">
        <strong>{animals.length} animals</strong>
        <input
          type="search"
          placeholder="Filter by ID or class"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="Filter animals"
        />
      </div>
      <div className="table-scroll">
        <table className="animals">
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th
                  key={c.key}
                  className={c.numeric ? 'num' : undefined}
                  aria-sort={sort.key === c.key ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}
                >
                  <button
                    type="button"
                    onClick={() => setSort((s) => ({ key: c.key, dir: s.key === c.key ? ((-s.dir) as 1 | -1) : 1 }))}
                  >
                    {c.label}
                    {sort.key === c.key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <tr
                key={a.id}
                className={a.id === selectedId ? 'selected' : undefined}
                onClick={() => onSelect(a.id)}
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && onSelect(a.id)}
              >
                <td>
                  <span className="dot dot--unique" />
                  {a.id}
                </td>
                <td>
                  {a.label}
                  {a.class_conflict && (
                    <span className="flag" title="Observations disagreed on the class">
                      ⚑
                    </span>
                  )}
                </td>
                <td className="num">{formatPercent(a.confidence)}</td>
                <td className="num">{a.observations}</td>
                <td className="num">{a.spread_m.toFixed(2)} m</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
