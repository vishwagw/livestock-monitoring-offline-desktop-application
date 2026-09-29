import type { FileRole, IngestedFile } from '@shared/types'

import { formatBytes } from '../lib/format'

export interface RoleFile extends IngestedFile {
  role: FileRole
}

const ROLE_KIND: Record<FileRole, IngestedFile['kind']> = {
  telemetry: 'telemetry',
  detections: 'detections',
  dataset: 'dataset'
}

interface Props {
  files: RoleFile[]
  role: FileRole
  disabled?: boolean
  onRemove: (id: string) => void
  empty: string
}

export function FileList({ files, role, disabled, onRemove, empty }: Props) {
  const items = files.filter((f) => f.role === role)
  if (items.length === 0) return <p className="filelist__empty">{empty}</p>
  return (
    <ul className="filelist" aria-label={`${role} files`}>
      {items.map((f) => {
        const mismatch = f.kind !== ROLE_KIND[role]
        return (
          <li key={f.id} className="filelist__item" title={f.path}>
            <span className={`badge badge--${mismatch ? 'warn' : f.kind}`}>{badgeLabel(f)}</span>
            <span className="filelist__name">{f.name}</span>
            <span className="filelist__meta">
              {mismatch ? 'Unrecognised format — the engine will try to read it' : f.detail} · {formatBytes(f.sizeBytes)}
            </span>
            <button
              type="button"
              className="icon-button"
              aria-label={`Remove ${f.name}`}
              disabled={disabled}
              onClick={() => onRemove(f.id)}
            >
              ×
            </button>
          </li>
        )
      })}
    </ul>
  )
}

function badgeLabel(f: IngestedFile): string {
  const ext = f.name.slice(f.name.lastIndexOf('.') + 1).toUpperCase()
  return ext.length <= 4 ? ext : 'FILE'
}
