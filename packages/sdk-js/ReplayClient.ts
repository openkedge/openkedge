import { ReplayResult, OpenKedgeEngine } from './types'

export class ReplayClient {
  constructor(private engine: OpenKedgeEngine) {}

  async getReplay(intentId: string): Promise<ReplayResult> {
    const events = await this.engine.getEvents(intentId)
    return {
      intentId,
      events
    }
  }
}
