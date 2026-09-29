import type { FileRole, IngestedFile } from '@shared/types'

export interface RoutedFile extends IngestedFile {
  role: FileRole
}

export interface RoutingResult {
  files: RoutedFile[]
  notices: string[]
}

const KIND_ROLE: Partial<Record<IngestedFile['kind'], FileRole>> = {
  telemetry: 'telemetry',
  detections: 'detections',
  dataset: 'dataset'
}

const ROLE_NAME: Record<FileRole, string> = {
  telemetry: 'Flight logs',
  detections: 'Detection logs',
  dataset: 'Engine dataset'
}

/**
 * Merge newly admitted files into the current list.
 *
 * A file goes where its detected kind says, even if it was dropped on the
 * other zone (with a notice), so pilots can drop everything anywhere.
 * Unrecognised files stay where they were dropped and the engine decides.
 * Only one engine dataset is kept at a time.
 */
export function routeFiles(current: RoutedFile[], incoming: IngestedFile[], droppedOn: FileRole): RoutingResult {
  const notices: string[] = []
  let files = [...current]
  for (const file of incoming) {
    const role = KIND_ROLE[file.kind] ?? droppedOn
    if (role !== droppedOn && KIND_ROLE[file.kind]) {
      notices.push(`${file.name} looks like ${article(role)} ${ROLE_NAME[role].toLowerCase().replace(/s$/, '')}; added to ${ROLE_NAME[role]}.`)
    }
    if (role === 'dataset') {
      const replaced = files.find((f) => f.role === 'dataset' && f.id !== file.id)
      if (replaced) notices.push(`${replaced.name} was replaced by ${file.name}.`)
      files = files.filter((f) => f.role !== 'dataset')
    }
    files = files.filter((f) => f.id !== file.id)
    files.push({ ...file, role })
  }
  return { files, notices }
}

function article(role: FileRole): string {
  return role === 'dataset' ? 'an' : 'a'
}

export function canRun(files: RoutedFile[]): { ok: boolean; reason: string | null } {
  if (files.some((f) => f.role === 'dataset')) return { ok: true, reason: null }
  const telemetry = files.some((f) => f.role === 'telemetry')
  const detections = files.some((f) => f.role === 'detections')
  if (!telemetry && !detections) return { ok: false, reason: 'Add flight logs and detection logs to begin.' }
  if (!telemetry) return { ok: false, reason: 'Add a flight log (.SRT or telemetry CSV).' }
  if (!detections) return { ok: false, reason: 'Add the AI bounding-box log.' }
  return { ok: true, reason: null }
}
