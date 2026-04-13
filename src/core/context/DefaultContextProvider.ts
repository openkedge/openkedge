import type { Intent } from '../../interfaces/contracts'

import type { ContextProvider } from './ContextProvider'

export class DefaultContextProvider implements ContextProvider {
  async resolve(intent: Intent): Promise<unknown> {
    return {
      intentType: intent.type,
      resolution: 'default-context-provider',
      observedAt: Date.now()
    }
  }
}
