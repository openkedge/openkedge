import { IntentProposal, PolicyDecision } from '../core'
import { Context } from '../context'

export type Rule = (proposal: IntentProposal, context: Context) => PolicyDecision | null
