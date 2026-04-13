import type { Intent } from '../../interfaces/contracts'

import type { ContextProvider } from './ContextProvider'

export class DefaultContextProvider implements ContextProvider {
  async resolve(_intent: Intent): Promise<unknown> {
    return {}
  }
}
