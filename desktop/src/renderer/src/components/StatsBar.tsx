import type { Report } from '@shared/types'

import { formatCount, formatPercent } from '../lib/format'

export function StatsBar({ report }: { report: Report | null }) {
  const s = report?.summary
  const raw = s?.input_detections ?? 0
  const unique = s?.unique_animals ?? 0
  const removed = s?.duplicate_detections ?? 0
  const labels = s ? Object.entries(s.label_counts) : []
  return (
    <section className="stats" aria-label="Headcount summary">
      <div className="stat stat--unique">
        <span className="stat__label">True headcount</span>
        <span className="stat__value" data-testid="unique-count">{report ? formatCount(unique) : '—'}</span>
        <span className="stat__sub">
          {labels.length ? labels.map(([k, v]) => `${formatCount(v)} ${k}`).join(' · ') : 'distinct animals'}
        </span>
      </div>
      <div className="stat stat--duplicate">
        <span className="stat__label">Duplicates removed</span>
        <span className="stat__value" data-testid="duplicate-count">{report ? formatCount(removed) : '—'}</span>
        <span className="stat__sub">{report && raw ? `${formatPercent(removed / raw)} of detections` : 'overlap re-sightings'}</span>
      </div>
      <div className="stat">
        <span className="stat__label">Raw detections</span>
        <span className="stat__value">{report ? formatCount(raw) : '—'}</span>
        <span className="stat__sub">{report ? `${formatCount(s!.frames)} frames with animals` : 'from the AI model'}</span>
      </div>
      <div className="stat">
        <span className="stat__label">Frame alignment</span>
        <span className="stat__value">
          {s?.registration.enabled ? `${(s.registration.mean_shift_m ?? 0).toFixed(2)} m` : report ? 'off' : '—'}
        </span>
        <span className="stat__sub">
          {s?.density?.mode === 'dense'
            ? `dense groups · ε ${s.density.eps_effective_m.toFixed(2)} m`
            : s?.registration.enabled
              ? `mean correction · ${s.registration.frames} frames`
              : 'GPS / heading drift'}
        </span>
      </div>
    </section>
  )
}
