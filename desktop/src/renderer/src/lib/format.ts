export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i++
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[i]}`
}

export const formatCount = (n: number): string => n.toLocaleString('en-US')

export const formatPercent = (fraction: number, digits = 1): string => `${(fraction * 100).toFixed(digits)}%`

export function formatCoord(lat: number, lon: number): string {
  const ns = lat >= 0 ? 'N' : 'S'
  const ew = lon >= 0 ? 'E' : 'W'
  return `${Math.abs(lat).toFixed(6)}° ${ns}, ${Math.abs(lon).toFixed(6)}° ${ew}`
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—'
  if (seconds < 60) return `${Math.round(seconds)} s`
  const m = Math.floor(seconds / 60)
  return `${m} min ${Math.round(seconds - m * 60)} s`
}

/** Remaining time from the overall rate so far; hidden until it is meaningful. */
export function estimateRemaining(percent: number, elapsedS: number): number | null {
  if (percent < 8 || percent >= 100 || elapsedS < 1) return null
  return (elapsedS * (100 - percent)) / percent
}
