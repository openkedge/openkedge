import type { ContextProvider, Intent } from '../../interfaces/contracts'

export class EmptyContextProvider
  implements ContextProvider<Record<string, never>>
{
  async resolve(_intent: Intent): Promise<Record<string, never>> {
    return {}
  }
}
