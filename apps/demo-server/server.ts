import express from 'express'
import cors from 'cors'
import { randomUUID } from 'node:crypto'

import { OpenKedgeEngine } from '../../src/core/engine/OpenKedgeEngine'
import { InMemoryEventStore } from '../../src/core/event/InMemoryEventStore'
import { IdentityManager } from '../../src/core/identity/IdentityManager'
import { OpenKedgeClient } from '../../src/sdk/client'
import type { ContextProvider } from '../../src/core/context/ContextProvider'
import type { PolicyEvaluator } from '../../src/core/evaluation/PolicyEvaluator'
import type { Executor } from '../../src/core/execution/Executor'
import type { IdentityProvider } from '../../src/core/identity/IdentityProvider'
import { BlastRadiusEstimator } from '../../src/core/blast/BlastRadiusEstimator'
import { BlastRadiusPolicy } from '../../src/core/blast/BlastRadiusPolicy'
import type { Intent, ExecutionIdentity } from '../../src/interfaces/contracts'

const app = express()
app.use(cors())
app.use(express.json())

// Mock EC2 Instances
const MOCK_EC2_INSTANCES = Array.from({ length: 35 }).map((_, i) => {
  const isCritical = i < 5
  return {
    id: `i-${String(i + 1).padStart(7, '0')}`,
    tags: {
      env: i < 15 ? 'prod' : 'dev',
      ...(isCritical ? { critical: 'true' } : {})
    }
  }
})

// Custom Policy to block terminating critical instances
class DemoPolicyEvaluator implements PolicyEvaluator {
  async evaluate(intent: Intent, context: any) {
    if (intent.type === 'ec2:TerminateInstances') {
      const targetIds = intent.payload as string[]
      const criticalTargets = targetIds.filter(id => {
        const inst = MOCK_EC2_INSTANCES.find(i => i.id === id)
        return inst?.tags.critical === 'true'
      })

      if (criticalTargets.length > 0) {
        return {
          allowed: false,
          reasons: [
            `Policy violation: Cannot terminate critical instances (${criticalTargets.join(', ')})`,
            'Safety Policy: BLOCKED'
          ],
          enrichedContext: context
        }
      }

      return {
        allowed: true,
        reasons: ['No policy violations detected. Instances are safe to terminate.'],
        enrichedContext: context
      }
    }
    return { allowed: true, reasons: [] }
  }
}

// Custom Context Provider
class DemoContextProvider implements ContextProvider {
  async resolve(intent: Intent) {
    if (intent.type === 'ec2:TerminateInstances') {
      const targetIds = intent.payload as string[]
      const instances = targetIds.map(id => MOCK_EC2_INSTANCES.find(i => i.id === id)).filter(Boolean)
      return {
        targets: instances,
        environment: 'demo-cloud'
      }
    }
    return {}
  }
}

class DemoBlastRadiusEstimator extends BlastRadiusEstimator {
  estimate(intent: Intent, context: any) {
    let score = 0
    let reasons: string[] = []
    let riskLevel: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL' = 'LOW'
    
    const count = context?.targets?.length || 0;
    
    if (intent.type === 'ec2:TerminateInstances' && context?.targets) {
      if (count > 20) {
        score = 90
        riskLevel = 'CRITICAL'
        reasons.push(`${count} instances targeted (Threshold: 20). Blast radius is CRITICAL.`)
      } else if (count > 5) {
        score = 60
        riskLevel = 'HIGH'
        reasons.push(`${count} instances targeted. Blast radius is HIGH.`)
      } else {
        score = 20
        riskLevel = 'LOW'
        reasons.push(`${count} instances targeted. Blast radius is LOW.`)
      }
    }

    return {
      score,
      riskLevel,
      resourceCount: count,
      resourceIds: context?.targets?.map((t: any) => t.id) || [],
      criticalResources: context?.targets?.filter((t: any) => t.tags.critical === 'true') || [],
      affectedServices: [],
      reasons
    }
  }
}

class DemoBlastRadiusPolicy extends BlastRadiusPolicy {
  evaluate(blast: any) {
    if (blast.score > 80) {
      return {
        allowed: false,
        reasons: ['CRITICAL blast radius exceeded acceptable limits. Action blocked.']
      }
    }
    return {
      allowed: true,
      reasons: ['Blast radius within acceptable limits.']
    }
  }
}

class DemoExecutor implements Executor {
  async execute(intent: Intent, context: unknown, identity: ExecutionIdentity) {
    return {
      success: true,
      error: undefined
    }
  }
}

class DemoIdentityProvider implements IdentityProvider {
  async issueIdentity(intent: Intent): Promise<ExecutionIdentity> {
    return {
      id: `demo-token-${randomUUID()}`,
      intentId: intent.id,
      issuedAt: Date.now(),
      expiresAt: Date.now() + 30000,
      metadata: { role: 'demo-execution-role', provider: 'demo-provider' },
      permissions: ['ec2:TerminateInstances']
    }
  }
  async revokeIdentity(identity: ExecutionIdentity) {}
}

const store = new InMemoryEventStore()
const engine = new OpenKedgeEngine(
  new DemoContextProvider(),
  new DemoPolicyEvaluator(),
  new DemoExecutor(),
  new IdentityManager(new DemoIdentityProvider(), store),
  store,
  new DemoBlastRadiusEstimator(),
  new DemoBlastRadiusPolicy()
)
const client = new OpenKedgeClient(engine, store)

app.get('/mock/ec2', (req, res) => {
  res.json(MOCK_EC2_INSTANCES)
})

app.post('/intent', async (req, res) => {
  try {
    const result = await client.submitIntent(req.body as Intent)
    res.json(result)
  } catch (err: any) {
    res.status(500).json({ error: err.message })
  }
})

app.get('/replay/:intentId', async (req, res) => {
  try {
    const replay = await client.replayIntent(req.params.intentId)
    res.json(replay)
  } catch (err: any) {
    res.status(404).json({ error: 'Replay not found' })
  }
})

const PORT = process.env.PORT || 3001
app.listen(PORT, () => {
  console.log(`Demo server running on port ${PORT}`)
})
