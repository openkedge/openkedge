import { EvidenceChain } from '../src/core/evidence/EvidenceChain'
import {
  EvidenceLink,
  GENESIS_PREV_LINK_HASH
} from '../src/core/evidence/EvidenceLink'
import { SovereignProxy } from '../src/core/evidence/SovereignProxy'

test('bind_justification cryptographically binds foreign reasoning to sovereign evaluation', () => {
  const draftLink = EvidenceLink.create({
    identity: {
      agentId: 'agent-ksa-west-1',
      traceId: 'trace-001'
    },
    intent: {
      foreignModel: 'gpt-5',
      abstractedPayload: 'scale resource-0001 to 4 replicas'
    },
    execution: {
      realResource: 'svc-riyadh-04',
      actualCommand: 'kubectl scale deployment svc-riyadh-04 --replicas=4',
      timestamp: '2026-04-13T12:00:00.000Z'
    }
  })

  const boundLink = draftLink.bind_justification(
    'Latency on resource-0001 exceeded 200ms for 5 minutes.',
    {
      evaluatorId: 'falcon-180b-ksa',
      policyVersion: 'nsp-2026.04',
      telemetrySnapshotHash: 'telemetry-hash-001',
      allowed: true,
      reasons: ['Latency threshold exceeded', 'Blast radius remains subnet-local'],
      matchedRules: ['latency-threshold', 'subnet-radius']
    }
  )

  expect(draftLink.intent.rawJustification).toBe('')
  expect(boundLink.intent.rawJustification).toContain('Latency on resource-0001')
  expect(boundLink.evaluation.decision).toBe('APPROVED')
  expect(boundLink.proof.justification_hash).toMatch(/^[a-f0-9]{64}$/)
  expect(boundLink.verify_integrity()).toBe(true)
  expect(Object.isFrozen(boundLink)).toBe(true)
  expect(Object.isFrozen(boundLink.intent)).toBe(true)
})

test('EvidenceChain appends immutable links with prev_link_hash lineage', () => {
  const first = EvidenceLink.create({
    identity: {
      agentId: 'agent-1',
      traceId: 'trace-immutable'
    },
    intent: {
      foreignModel: 'gpt-5',
      rawJustification: 'Increase capacity for resource-0001.',
      abstractedPayload: 'scale resource-0001 to 4 replicas'
    },
    evaluation: {
      evaluatorId: 'local-evaluator',
      policyVersion: 'policy-v1',
      telemetrySnapshotHash: 'snap-1',
      decision: 'APPROVED'
    },
    execution: {
      realResource: 'svc-a',
      actualCommand: 'scale svc-a',
      timestamp: '2026-04-13T12:00:00.000Z'
    }
  })
  const second = EvidenceLink.create({
    identity: {
      agentId: 'agent-1',
      traceId: 'trace-immutable'
    },
    intent: {
      foreignModel: 'gpt-5',
      rawJustification: 'Drain resource-0002 for patching.',
      abstractedPayload: 'drain resource-0002'
    },
    evaluation: {
      evaluatorId: 'local-evaluator',
      policyVersion: 'policy-v1',
      telemetrySnapshotHash: 'snap-2',
      decision: 'APPROVED'
    },
    execution: {
      realResource: 'node-b',
      actualCommand: 'kubectl drain node-b',
      timestamp: '2026-04-13T12:05:00.000Z'
    }
  })

  const chain = new EvidenceChain()
  const nextChain = chain.append(first).append(second)

  expect(chain.links).toHaveLength(0)
  expect(nextChain.links).toHaveLength(2)
  expect(nextChain.links[0].proof.prev_link_hash).toBe(GENESIS_PREV_LINK_HASH)
  expect(nextChain.links[1].proof.prev_link_hash).toBe(
    nextChain.links[0].proof.link_hash
  )
  expect(nextChain.verify_integrity()).toBe(true)
})

test('SovereignProxy masks and unmasks sensitive resources deterministically', () => {
  const proxy = new SovereignProxy()

  const masked = proxy.mask_resource('dc-riyadh-cooling-04')

  expect(masked).toBe('resource-0001')
  expect(proxy.mask_resource('dc-riyadh-cooling-04')).toBe(masked)
  expect(proxy.unmask_resource(masked)).toBe('dc-riyadh-cooling-04')
  expect(() => proxy.unmask_resource('resource-9999')).toThrow(
    'Unknown masked resource: resource-9999'
  )
})

test('EvidenceChain exports JSON-LD for auditability', () => {
  const chain = new EvidenceChain().append({
    identity: {
      agentId: 'agent-jsonld',
      traceId: 'trace-jsonld'
    },
    intent: {
      foreignModel: 'gpt-5',
      rawJustification: 'Shift traffic away from resource-0001.',
      abstractedPayload: 'shift traffic from resource-0001'
    },
    evaluation: {
      evaluatorId: 'sovereign-evaluator',
      policyVersion: 'ksa-2026.04',
      telemetrySnapshotHash: 'snap-jsonld',
      decision: 'APPROVED'
    },
    execution: {
      realResource: 'ingress-west-01',
      actualCommand: 'shift ingress-west-01 traffic',
      timestamp: '2026-04-13T13:00:00.000Z'
    },
    proof: {
      signature: 'kedge-signature-001'
    }
  })

  const parsed = JSON.parse(chain.export_jsonld()) as {
    '@context': Record<string, string>
    '@type': string
    hasPart: Array<{
      proof: {
        prev_link_hash: string
        link_hash: string
      }
    }>
  }

  expect(parsed['@type']).toBe('okg:EvidenceChain')
  expect(parsed['@context'].okg).toBe('https://openkedge.dev/ns/evidence#')
  expect(parsed.hasPart).toHaveLength(1)
  expect(parsed.hasPart[0].proof.prev_link_hash).toBe(GENESIS_PREV_LINK_HASH)
  expect(parsed.hasPart[0].proof.link_hash).toMatch(/^[a-f0-9]{64}$/)
})
