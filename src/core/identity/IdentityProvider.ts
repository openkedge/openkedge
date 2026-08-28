import type { Intent } from '../../interfaces/contracts'

import type { ExecutionIdentity } from './Identity'
import type { ExecutionContract } from '../governance/types'

export interface IdentityProvider {
  issueIdentity(intent: Intent, contract?: ExecutionContract): Promise<ExecutionIdentity>
  revokeIdentity(identity: ExecutionIdentity): Promise<void>
}
