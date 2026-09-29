import { useEffect, useState } from 'react'

import type { ProgressEvent } from '@shared/types'

import { estimateRemaining, formatCount, formatDuration } from '../lib/format'

interface Props {
  progress: ProgressEvent | null
  startedAt: number
  onCancel: () => void
}

const COUNTERS: { key: string; label: string }[] = [
  { key: 'raw_detections', label: 'Detections read' },
  { key: 'matched', label: 'Matched' },
  { key: 'frames', label: 'Frames' },
  { key: 'detections', label: 'Georeferenced' },
  { key: 'animals', label: 'Animals so far' }
]

export function ProgressPanel({ progress, startedAt, onCancel }: Props) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(t)
  }, [])

  const percent = progress?.percent ?? 0
  const elapsed = (now - startedAt) / 1000
  const remaining = estimateRemaining(percent, elapsed)
  const stages = progress?.stages ?? []
  const current = progress?.stageIndex ?? -1
  const counters = COUNTERS.filter((c) => progress?.counts[c.key] !== undefined)

  return (
    <div className="progress-panel" aria-live="polite">
      <div className="progress-panel__head">
        <span className="progress-panel__percent" data-testid="progress-percent">
          {Math.floor(percent)}%
        </span>
        <span className="muted small">
          {formatDuration(elapsed)} elapsed
          {remaining !== null && ` · ~${formatDuration(remaining)} left`}
        </span>
      </div>
      <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.floor(percent)}>
        <div className="progress__bar" style={{ width: `${Math.max(2, percent)}%` }} />
      </div>
      <p className="progress-panel__message">{progress?.message || 'Starting processing engine…'}</p>

      {stages.length > 0 && (
        <ol className="stages">
          {stages.map((label, i) => {
            const state = i < current ? 'done' : i === current ? 'active' : 'pending'
            return (
              <li key={label} className={`stage stage--${state}`}>
                <span className="stage__icon" aria-hidden>
                  {state === 'done' ? '✓' : state === 'active' ? '' : '○'}
                </span>
                <span className="stage__label">{label}</span>
                {state === 'active' && <span className="stage__pct">{Math.floor(progress?.stagePercent ?? 0)}%</span>}
              </li>
            )
          })}
        </ol>
      )}

      {counters.length > 0 && (
        <dl className="counters">
          {counters.map((c) => (
            <div key={c.key}>
              <dt>{c.label}</dt>
              <dd>{formatCount(progress!.counts[c.key])}</dd>
            </div>
          ))}
        </dl>
      )}

      <button type="button" className="button button--ghost" onClick={onCancel}>
        Cancel
      </button>
    </div>
  )
}
