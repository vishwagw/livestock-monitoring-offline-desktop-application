import { useId, type ReactNode } from 'react'

import type { AppSettings, CameraSettings, ClusteringSettings, FlightSettings } from '@shared/types'

import { CAMERA_PRESETS, matchPreset } from '../lib/presets'

interface Props {
  settings: AppSettings
  disabled?: boolean
  rawMode: boolean
  onChange: (patch: Partial<Pick<AppSettings, 'camera' | 'flight' | 'clustering'>>) => void
}

function Field({ label, hint, children }: { label: string; hint?: string; children: (id: string) => ReactNode }) {
  const id = useId()
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {hint && <small>{hint}</small>}
    </div>
  )
}

function NumberInput(props: {
  id: string
  value: number | null
  min: number
  max: number
  step?: number
  disabled?: boolean
  placeholder?: string
  onChange: (value: number | null) => void
}) {
  return (
    <input
      id={props.id}
      type="number"
      inputMode="decimal"
      value={props.value ?? ''}
      min={props.min}
      max={props.max}
      step={props.step ?? 1}
      placeholder={props.placeholder}
      disabled={props.disabled}
      onChange={(e) => {
        const raw = e.target.value
        if (raw === '') return props.onChange(null)
        const v = Number(raw)
        if (Number.isFinite(v)) props.onChange(Math.min(props.max, Math.max(props.min, v)))
      }}
    />
  )
}

export function SettingsPanel({ settings, disabled, rawMode, onChange }: Props) {
  const { camera, flight, clustering } = settings
  const setCamera = (patch: Partial<CameraSettings>): void => onChange({ camera: { ...camera, ...patch } })
  const setFlight = (patch: Partial<FlightSettings>): void => onChange({ flight: { ...flight, ...patch } })
  const setClustering = (patch: Partial<ClusteringSettings>): void =>
    onChange({ clustering: { ...clustering, ...patch } })
  const preset = matchPreset(camera)

  return (
    <div className="settings">
      <fieldset disabled={disabled || !rawMode} className="settings__group">
        <legend>Camera</legend>
        {!rawMode && <p className="muted small">The engine dataset carries its own camera metadata.</p>}
        <Field label="Drone camera">
          {(id) => (
            <select
              id={id}
              value={preset}
              onChange={(e) => {
                const p = CAMERA_PRESETS.find((c) => c.id === e.target.value)
                if (p) setCamera(p.camera)
              }}
            >
              {CAMERA_PRESETS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
              <option value="custom">Custom…</option>
            </select>
          )}
        </Field>
        <div className="field-row">
          <Field label="Width (px)">
            {(id) => (
              <NumberInput id={id} value={camera.imageWidth} min={16} max={50000}
                onChange={(v) => v !== null && setCamera({ imageWidth: Math.round(v) })} />
            )}
          </Field>
          <Field label="Height (px)">
            {(id) => (
              <NumberInput id={id} value={camera.imageHeight} min={16} max={50000}
                onChange={(v) => v !== null && setCamera({ imageHeight: Math.round(v) })} />
            )}
          </Field>
        </div>
        <div className="field-row">
          <Field label="Field of view (°)">
            {(id) => (
              <NumberInput id={id} value={camera.fovDeg} min={1} max={179} step={0.1}
                onChange={(v) => v !== null && setCamera({ fovDeg: v })} />
            )}
          </Field>
          <Field label="Measured">
            {(id) => (
              <select id={id} value={camera.fovType}
                onChange={(e) => setCamera({ fovType: e.target.value as CameraSettings['fovType'] })}>
                <option value="diagonal">Diagonal</option>
                <option value="horizontal">Horizontal</option>
                <option value="vertical">Vertical</option>
              </select>
            )}
          </Field>
        </div>
      </fieldset>

      <fieldset disabled={disabled || !rawMode} className="settings__group">
        <legend>Flight defaults</legend>
        <div className="field-row">
          <Field label="Gimbal pitch (°)" hint="Used when the log has none">
            {(id) => (
              <NumberInput id={id} value={flight.defaultPitchDeg} min={-90} max={0} step={0.5}
                onChange={(v) => v !== null && setFlight({ defaultPitchDeg: v })} />
            )}
          </Field>
          <Field label="Altitude AGL (m)" hint="Only if not logged">
            {(id) => (
              <NumberInput id={id} value={flight.defaultAltitudeM} min={1} max={2000} step={0.5} placeholder="From log"
                onChange={(v) => setFlight({ defaultAltitudeM: v })} />
            )}
          </Field>
        </div>
        <div className="field-row">
          <Field label="Box ground point">
            {(id) => (
              <select id={id} value={flight.bboxAnchor}
                onChange={(e) => setFlight({ bboxAnchor: e.target.value as FlightSettings['bboxAnchor'] })}>
                <option value="center">Centre (top-down)</option>
                <option value="bottom">Bottom edge (oblique)</option>
              </select>
            )}
          </Field>
          <Field label="Video FPS" hint="For frame-number logs">
            {(id) => (
              <NumberInput id={id} value={flight.videoFps} min={1} max={240} step={0.01}
                onChange={(v) => v !== null && setFlight({ videoFps: v })} />
            )}
          </Field>
        </div>
        <label className="check">
          <input type="checkbox" checked={flight.deriveHeading}
            onChange={(e) => setFlight({ deriveHeading: e.target.checked })} />
          Derive heading from the GPS track when yaw is not logged
        </label>
        <Field label="YOLO class names" hint="Comma-separated, in class-id order">
          {(id) => (
            <input id={id} type="text" value={flight.classNames} placeholder="cattle, sheep, horse"
              maxLength={2000} onChange={(e) => setFlight({ classNames: e.target.value.replace(/[\r\n]/g, ' ') })} />
          )}
        </Field>
      </fieldset>

      <fieldset disabled={disabled} className="settings__group">
        <legend>De-duplication</legend>
        <Field label="Cluster radius ε (m)" hint="Animal body envelope; 2.0 m for cattle">
          {(id) => (
            <NumberInput id={id} value={clustering.epsM} min={0.1} max={50} step={0.1}
              onChange={(v) => v !== null && setClustering({ epsM: v })} />
          )}
        </Field>
        <label className="check">
          <input type="checkbox" checked={clustering.registration}
            onChange={(e) => setClustering({ registration: e.target.checked })} />
          Align overlapping frames (corrects GPS / heading drift)
        </label>
        <label className="check">
          <input type="checkbox" checked={clustering.frameExclusivity}
            onChange={(e) => setClustering({ frameExclusivity: e.target.checked })} />
          Never merge two animals seen in the same image
        </label>
        <label className="check">
          <input type="checkbox" checked={clustering.adaptiveDensity}
            onChange={(e) => setClustering({ adaptiveDensity: e.target.checked })} />
          Adapt to tightly packed animals (sheep yards, feedlots)
        </label>
      </fieldset>
    </div>
  )
}
