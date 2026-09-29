import type { CameraSettings } from '@shared/types'

export interface CameraPreset {
  id: string
  label: string
  camera: CameraSettings
}

/** Wide-camera still-image specifications from the manufacturers' data sheets. */
export const CAMERA_PRESETS: CameraPreset[] = [
  { id: 'm3e', label: 'DJI Mavic 3 Enterprise (wide)', camera: { imageWidth: 5280, imageHeight: 3956, fovDeg: 84, fovType: 'diagonal' } },
  { id: 'm3t', label: 'DJI Mavic 3 Thermal (wide)', camera: { imageWidth: 4000, imageHeight: 3000, fovDeg: 84, fovType: 'diagonal' } },
  { id: 'm30t', label: 'DJI Matrice 30T (wide)', camera: { imageWidth: 4000, imageHeight: 3000, fovDeg: 84, fovType: 'diagonal' } },
  { id: 'p4rtk', label: 'DJI Phantom 4 RTK', camera: { imageWidth: 5472, imageHeight: 3648, fovDeg: 84, fovType: 'diagonal' } },
  { id: 'mini4', label: 'DJI Mini 4 Pro (12 MP)', camera: { imageWidth: 4032, imageHeight: 3024, fovDeg: 82.1, fovType: 'diagonal' } },
  { id: 'air3', label: 'DJI Air 3 (wide, 12 MP)', camera: { imageWidth: 4032, imageHeight: 3024, fovDeg: 82, fovType: 'diagonal' } },
  { id: 'uhd', label: '4K video frames (3840×2160)', camera: { imageWidth: 3840, imageHeight: 2160, fovDeg: 82, fovType: 'diagonal' } }
]

export function matchPreset(camera: CameraSettings): string {
  const hit = CAMERA_PRESETS.find(
    (p) =>
      p.camera.imageWidth === camera.imageWidth &&
      p.camera.imageHeight === camera.imageHeight &&
      p.camera.fovDeg === camera.fovDeg &&
      p.camera.fovType === camera.fovType
  )
  return hit?.id ?? 'custom'
}
