import type { Intent } from '../../interfaces/contracts'

import type { ExecutionIdentity } from './Identity'

export interface IdentityProvider {
  issueIdentity(intent: Intent): Promise<ExecutionIdentity>
  revokeIdentity(identity: ExecutionIdentity): Promise<void>
}
