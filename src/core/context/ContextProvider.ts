import type { Intent } from '../../interfaces/contracts'

export interface ContextProvider {
  resolve(intent: Intent): Promise<unknown>
}
