import { useRef, useState, type DragEvent, type ReactNode } from 'react'

import type { FileRole } from '@shared/types'

interface Props {
  role: FileRole
  title: string
  hint: string
  icon: ReactNode
  disabled?: boolean
  onPaths: (paths: string[], role: FileRole) => void
  onBrowse: (role: FileRole) => void
}

export function DropZone({ role, title, hint, icon, disabled, onPaths, onBrowse }: Props) {
  const [active, setActive] = useState(false)
  const depth = useRef(0)

  const hasFiles = (e: DragEvent): boolean => Array.from(e.dataTransfer.types).includes('Files')

  const onDragEnter = (e: DragEvent): void => {
    if (disabled || !hasFiles(e)) return
    e.preventDefault()
    depth.current += 1
    setActive(true)
  }
  const onDragLeave = (e: DragEvent): void => {
    if (disabled || !hasFiles(e)) return
    depth.current = Math.max(0, depth.current - 1)
    if (depth.current === 0) setActive(false)
  }
  const onDragOver = (e: DragEvent): void => {
    if (disabled || !hasFiles(e)) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }
  const onDrop = (e: DragEvent): void => {
    e.preventDefault()
    depth.current = 0
    setActive(false)
    if (disabled) return
    const paths = Array.from(e.dataTransfer.files)
      .map((f) => window.livestock.pathForFile(f))
      .filter((p) => p.length > 0)
    if (paths.length) onPaths(paths, role)
  }

  return (
    <div
      className={`dropzone${active ? ' dropzone--active' : ''}${disabled ? ' dropzone--disabled' : ''}`}
      data-role={role}
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDragOver={onDragOver}
      onDrop={onDrop}
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-label={`${title}: drop files or press Enter to browse`}
      onClick={() => !disabled && onBrowse(role)}
      onKeyDown={(e) => {
        if (!disabled && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault()
          onBrowse(role)
        }
      }}
    >
      <div className="dropzone__icon" aria-hidden>
        {icon}
      </div>
      <div className="dropzone__text">
        <strong>{title}</strong>
        <span>{active ? 'Release to add' : hint}</span>
      </div>
    </div>
  )
}
