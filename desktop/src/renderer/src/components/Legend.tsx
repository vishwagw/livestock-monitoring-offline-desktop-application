import type { LayerVisibility } from './MapView'

interface Props {
  visibility: LayerVisibility
  counts: { unique: number; duplicates: number; frames: number } | null
  onToggle: (key: keyof LayerVisibility) => void
}

const ITEMS: { key: keyof LayerVisibility; label: string; swatch: string }[] = [
  { key: 'unique', label: 'Distinct animals', swatch: 'swatch--unique' },
  { key: 'duplicates', label: 'Removed duplicates', swatch: 'swatch--duplicate' },
  { key: 'flightPath', label: 'Flight path', swatch: 'swatch--path' },
  { key: 'frames', label: 'Capture points', swatch: 'swatch--frame' }
]

export function Legend({ visibility, counts, onToggle }: Props) {
  const countFor = (key: keyof LayerVisibility): number | null => {
    if (!counts) return null
    if (key === 'unique') return counts.unique
    if (key === 'duplicates') return counts.duplicates
    if (key === 'frames') return counts.frames
    return null
  }
  return (
    <div className="legend" role="group" aria-label="Map layers">
      {ITEMS.map((item) => {
        const n = countFor(item.key)
        return (
          <label key={item.key} className={`legend__item${visibility[item.key] ? '' : ' legend__item--off'}`}>
            <input type="checkbox" checked={visibility[item.key]} onChange={() => onToggle(item.key)} />
            <span className={`swatch ${item.swatch}`} aria-hidden />
            <span>{item.label}</span>
            {n !== null && <span className="legend__count">{n.toLocaleString('en-US')}</span>}
          </label>
        )
      })}
    </div>
  )
}
