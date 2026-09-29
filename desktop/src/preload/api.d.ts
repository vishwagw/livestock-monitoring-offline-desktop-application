import type { LivestockApi } from '../shared/types'

declare global {
  interface Window {
    livestock: LivestockApi
  }
}

export {}
