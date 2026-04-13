export interface IntentMetadata {
  actor: string
  timestamp: number
}

export interface Intent {
  id: string
  type: string
  payload: unknown
  metadata: IntentMetadata
}

export interface EvaluationResult {
  allowed: boolean
  reasons: string[]
  enrichedContext?: unknown
}

export interface ExecutionResult {
  success: boolean
  result?: unknown
  error?: string
}

export interface Event<TPayload = unknown> {
  id: string
  type: string
  timestamp: number
  intentId: string
  payload: TPayload
}

export interface ContextProvider<TContext = unknown> {
  resolve(intent: Intent): Promise<TContext>
}

export interface PolicyEvaluator<TContext = unknown> {
  evaluate(intent: Intent, context: TContext): Promise<EvaluationResult>
}

export interface Executor<TContext = unknown> {
  execute(intent: Intent, context: TContext): Promise<ExecutionResult>
}

export interface EventStore {
  append(event: Event): Promise<void>
  query(intentId: string): Promise<Event[]>
}
