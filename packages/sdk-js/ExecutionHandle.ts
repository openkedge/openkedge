import { PolicyDecision, TruthEvent } from '../core'
import { OpenKedgeEngine, ReplayResult, SummaryResult } from './types'
import { ReplayClient } from './ReplayClient'

export class ExecutionHandle<TResult> {
  public success: boolean
  public result?: TResult
  public error?: string
  public intentId: string

  private replayClient: ReplayClient
  private decision: PolicyDecision

  constructor(
    intentId: string,
    decision: PolicyDecision,
    engine: OpenKedgeEngine,
    result?: TResult
  ) {
    this.intentId = intentId
    this.decision = decision
    this.replayClient = new ReplayClient(engine)

    this.success = decision.allowed
    if (!this.success) {
      this.error = decision.reasons.join(', ')
    } else {
      this.result = result
    }
  }

  async replay(): Promise<ReplayResult> {
    return this.replayClient.getReplay(this.intentId)
  }

  async events(): Promise<TruthEvent[]> {
    const replay = await this.replayClient.getReplay(this.intentId)
    return replay.events
  }

  async summary(): Promise<SummaryResult> {
    return {
      outcome: this.decision.allowed ? 'EXECUTED' : 'BLOCKED',
      reason: this.decision.reasons.join(', ')
    }
  }
}
