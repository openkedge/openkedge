import type { ReplayResult } from './types'

const baseTimestamp = Date.parse('2026-04-12T18:30:00.000Z')

function eventHash(sequence: number): string {
  return `hash-${sequence}`
}

function buildAllowedReplay(): ReplayResult {
  return {
    intentId: 'allowed-demo',
    originalIntent: {
      id: 'allowed-demo',
      type: 'ec2:TerminateInstances',
      payload: ['i-0aa11bb22'],
      metadata: {
        actor: 'agent-1',
        timestamp: baseTimestamp
      }
    },
    reasoningTrail: [
      'Intent submitted by actor=agent-1',
      'Intent type=ec2:TerminateInstances',
      'Context provider resolved current execution context',
      'Intent targets 1 resource',
      'Blast radius evaluated as LOW (1 resource)',
      'No critical instances detected',
      'Low blast radius accepted by policy',
      'Intent approved for execution',
      'Execution identity bound to intent type ec2:TerminateInstances',
      'Executor completed successfully'
    ],
    replayable: true,
    reconstructed: {
      contextSnapshot: {
        instances: [
          {
            instanceId: 'i-0aa11bb22',
            state: 'running',
            tags: {
              env: 'dev',
              critical: 'false'
            }
          }
        ]
      },
      blastRadius: {
        resourceCount: 1,
        resourceIds: ['i-0aa11bb22'],
        riskLevel: 'LOW',
        reasons: [
          'Intent targets 1 resource',
          'Blast radius evaluated as LOW (1 resource)'
        ]
      },
      evaluationResult: {
        allowed: true,
        reasons: [
          'No critical instances detected',
          'Intent targets 1 resource',
          'Blast radius evaluated as LOW (1 resource)',
          'Low blast radius accepted by policy'
        ]
      },
      executionResult: {
        success: true,
        result: {
          terminated: ['i-0aa11bb22']
        }
      },
      finalOutcome: 'allowed'
    },
    steps: [],
    integrity: {
      valid: true
    },
    events: [
      {
        id: 'allowed-1',
        type: 'IntentReceived',
        timestamp: baseTimestamp,
        intentId: 'allowed-demo',
        sequence: 1,
        previousEventHash: null,
        currentHash: eventHash(1),
        payload: {
          intentSnapshot: {
            id: 'allowed-demo',
            type: 'ec2:TerminateInstances',
            payload: ['i-0aa11bb22'],
            metadata: {
              actor: 'agent-1',
              timestamp: baseTimestamp
            }
          },
          reasoningTrail: [
            'Intent submitted by actor=agent-1',
            'Intent type=ec2:TerminateInstances'
          ]
        }
      },
      {
        id: 'allowed-2',
        type: 'ContextResolved',
        timestamp: baseTimestamp + 1500,
        intentId: 'allowed-demo',
        sequence: 2,
        previousEventHash: eventHash(1),
        currentHash: eventHash(2),
        payload: {
          intentSnapshot: {
            id: 'allowed-demo',
            type: 'ec2:TerminateInstances',
            payload: ['i-0aa11bb22'],
            metadata: {
              actor: 'agent-1',
              timestamp: baseTimestamp
            }
          },
          contextSnapshot: {
            instances: [
              {
                instanceId: 'i-0aa11bb22',
                state: 'running',
                tags: {
                  env: 'dev',
                  critical: 'false'
                }
              }
            ]
          },
          reasoningTrail: ['Context provider resolved current execution context']
        }
      },
      {
        id: 'allowed-3',
        type: 'BlastRadiusEvaluated',
        timestamp: baseTimestamp + 2600,
        intentId: 'allowed-demo',
        sequence: 3,
        previousEventHash: eventHash(2),
        currentHash: eventHash(3),
        payload: {
          intentSnapshot: {
            id: 'allowed-demo',
            type: 'ec2:TerminateInstances',
            payload: ['i-0aa11bb22'],
            metadata: {
              actor: 'agent-1',
              timestamp: baseTimestamp
            }
          },
          blastRadius: {
            resourceCount: 1,
            resourceIds: ['i-0aa11bb22'],
            riskLevel: 'LOW',
            reasons: [
              'Intent targets 1 resource',
              'Blast radius evaluated as LOW (1 resource)'
            ]
          },
          reasoningTrail: [
            'Intent targets 1 resource',
            'Blast radius evaluated as LOW (1 resource)'
          ]
        }
      },
      {
        id: 'allowed-4',
        type: 'EvaluationCompleted',
        timestamp: baseTimestamp + 3400,
        intentId: 'allowed-demo',
        sequence: 4,
        previousEventHash: eventHash(3),
        currentHash: eventHash(4),
        payload: {
          intentSnapshot: {
            id: 'allowed-demo',
            type: 'ec2:TerminateInstances',
            payload: ['i-0aa11bb22'],
            metadata: {
              actor: 'agent-1',
              timestamp: baseTimestamp
            }
          },
          blastRadius: {
            resourceCount: 1,
            resourceIds: ['i-0aa11bb22'],
            riskLevel: 'LOW',
            reasons: [
              'Intent targets 1 resource',
              'Blast radius evaluated as LOW (1 resource)'
            ]
          },
          evaluationResult: {
            allowed: true,
            reasons: [
              'No critical instances detected',
              'Intent targets 1 resource',
              'Blast radius evaluated as LOW (1 resource)',
              'Low blast radius accepted by policy'
            ]
          },
          reasoningTrail: [
            'No critical instances detected',
            'Intent targets 1 resource',
            'Blast radius evaluated as LOW (1 resource)',
            'Low blast radius accepted by policy',
            'Intent approved for execution'
          ]
        }
      },
      {
        id: 'allowed-5',
        type: 'ExecutionCompleted',
        timestamp: baseTimestamp + 4800,
        intentId: 'allowed-demo',
        sequence: 5,
        previousEventHash: eventHash(4),
        currentHash: eventHash(5),
        payload: {
          intentSnapshot: {
            id: 'allowed-demo',
            type: 'ec2:TerminateInstances',
            payload: ['i-0aa11bb22'],
            metadata: {
              actor: 'agent-1',
              timestamp: baseTimestamp
            }
          },
          executionResult: {
            success: true,
            result: {
              terminated: ['i-0aa11bb22']
            }
          },
          reasoningTrail: ['Executor completed successfully']
        }
      }
    ]
  }
}

function buildBlockedReplay(): ReplayResult {
  return {
    intentId: 'blocked-demo',
    originalIntent: {
      id: 'blocked-demo',
      type: 'ec2:TerminateInstances',
      payload: ['i-prod-001'],
      metadata: {
        actor: 'agent-2',
        timestamp: baseTimestamp + 10_000
      }
    },
    reasoningTrail: [
      'Intent submitted by actor=agent-2',
      'Context provider resolved current execution context',
      'Intent targets 1 resource',
      'Critical instances detected: i-prod-001',
      'Blast radius evaluated as CRITICAL (1 resource)',
      'Blocked termination of critical instances: i-prod-001',
      'Blocked due to CRITICAL blast radius',
      'Intent denied before execution'
    ],
    replayable: true,
    reconstructed: {
      contextSnapshot: {
        instances: [
          {
            instanceId: 'i-prod-001',
            state: 'running',
            tags: {
              env: 'prod',
              critical: 'true'
            }
          }
        ]
      },
      blastRadius: {
        resourceCount: 1,
        resourceIds: ['i-prod-001'],
        riskLevel: 'CRITICAL',
        reasons: [
          'Intent targets 1 resource',
          'Critical instances detected: i-prod-001',
          'Blast radius evaluated as CRITICAL (1 resource)'
        ]
      },
      evaluationResult: {
        allowed: false,
        reasons: [
          'Blocked termination of critical instances: i-prod-001',
          'Intent targets 1 resource',
          'Critical instances detected: i-prod-001',
          'Blast radius evaluated as CRITICAL (1 resource)',
          'Blocked due to CRITICAL blast radius'
        ]
      },
      executionResult: {
        success: false,
        error: 'Blocked by policy'
      },
      finalOutcome: 'blocked'
    },
    steps: [],
    integrity: {
      valid: true
    },
    events: [
      {
        id: 'blocked-1',
        type: 'IntentReceived',
        timestamp: baseTimestamp + 10_000,
        intentId: 'blocked-demo',
        sequence: 1,
        previousEventHash: null,
        currentHash: eventHash(11),
        payload: {
          intentSnapshot: {
            id: 'blocked-demo',
            type: 'ec2:TerminateInstances',
            payload: ['i-prod-001'],
            metadata: {
              actor: 'agent-2',
              timestamp: baseTimestamp + 10_000
            }
          },
          reasoningTrail: ['Intent submitted by actor=agent-2']
        }
      },
      {
        id: 'blocked-2',
        type: 'ContextResolved',
        timestamp: baseTimestamp + 11_200,
        intentId: 'blocked-demo',
        sequence: 2,
        previousEventHash: eventHash(11),
        currentHash: eventHash(12),
        payload: {
          intentSnapshot: {
            id: 'blocked-demo',
            type: 'ec2:TerminateInstances',
            payload: ['i-prod-001'],
            metadata: {
              actor: 'agent-2',
              timestamp: baseTimestamp + 10_000
            }
          },
          contextSnapshot: {
            instances: [
              {
                instanceId: 'i-prod-001',
                state: 'running',
                tags: {
                  env: 'prod',
                  critical: 'true'
                }
              }
            ]
          },
          reasoningTrail: ['Context provider resolved current execution context']
        }
      },
      {
        id: 'blocked-3',
        type: 'BlastRadiusEvaluated',
        timestamp: baseTimestamp + 12_000,
        intentId: 'blocked-demo',
        sequence: 3,
        previousEventHash: eventHash(12),
        currentHash: eventHash(13),
        payload: {
          intentSnapshot: {
            id: 'blocked-demo',
            type: 'ec2:TerminateInstances',
            payload: ['i-prod-001'],
            metadata: {
              actor: 'agent-2',
              timestamp: baseTimestamp + 10_000
            }
          },
          blastRadius: {
            resourceCount: 1,
            resourceIds: ['i-prod-001'],
            riskLevel: 'CRITICAL',
            reasons: [
              'Intent targets 1 resource',
              'Critical instances detected: i-prod-001',
              'Blast radius evaluated as CRITICAL (1 resource)'
            ]
          },
          reasoningTrail: [
            'Intent targets 1 resource',
            'Critical instances detected: i-prod-001',
            'Blast radius evaluated as CRITICAL (1 resource)'
          ]
        }
      },
      {
        id: 'blocked-4',
        type: 'EvaluationCompleted',
        timestamp: baseTimestamp + 13_000,
        intentId: 'blocked-demo',
        sequence: 4,
        previousEventHash: eventHash(13),
        currentHash: eventHash(14),
        payload: {
          intentSnapshot: {
            id: 'blocked-demo',
            type: 'ec2:TerminateInstances',
            payload: ['i-prod-001'],
            metadata: {
              actor: 'agent-2',
              timestamp: baseTimestamp + 10_000
            }
          },
          evaluationResult: {
            allowed: false,
            reasons: [
              'Blocked termination of critical instances: i-prod-001',
              'Intent targets 1 resource',
              'Critical instances detected: i-prod-001',
              'Blast radius evaluated as CRITICAL (1 resource)',
              'Blocked due to CRITICAL blast radius'
            ]
          },
          blastRadius: {
            resourceCount: 1,
            resourceIds: ['i-prod-001'],
            riskLevel: 'CRITICAL',
            reasons: [
              'Intent targets 1 resource',
              'Critical instances detected: i-prod-001',
              'Blast radius evaluated as CRITICAL (1 resource)'
            ]
          },
          reasoningTrail: [
            'Blocked termination of critical instances: i-prod-001',
            'Blocked due to CRITICAL blast radius',
            'Intent denied before execution'
          ]
        }
      },
      {
        id: 'blocked-5',
        type: 'ExecutionSkipped',
        timestamp: baseTimestamp + 13_200,
        intentId: 'blocked-demo',
        sequence: 5,
        previousEventHash: eventHash(14),
        currentHash: eventHash(15),
        payload: {
          intentSnapshot: {
            id: 'blocked-demo',
            type: 'ec2:TerminateInstances',
            payload: ['i-prod-001'],
            metadata: {
              actor: 'agent-2',
              timestamp: baseTimestamp + 10_000
            }
          },
          executionResult: {
            success: false,
            error: 'Blocked by policy'
          },
          reasoningTrail: [
            'Execution was intentionally skipped because evaluation denied the intent'
          ]
        }
      }
    ]
  }
}

function buildCriticalBlastReplay(): ReplayResult {
  return {
    intentId: 'critical-blast-demo',
    originalIntent: {
      id: 'critical-blast-demo',
      type: 'ec2:TerminateInstances',
      payload: Array.from({ length: 12 }, (_, index) => `i-wide-${index + 1}`),
      metadata: {
        actor: 'agent-3',
        timestamp: baseTimestamp + 20_000
      }
    },
    reasoningTrail: [
      'Intent submitted by actor=agent-3',
      'Context provider resolved current execution context',
      'Intent targets 12 resources',
      'Blast radius evaluated as HIGH (12 resources)',
      'High-risk mutation allowed with warning',
      'Intent approved for execution',
      'Executor completed successfully'
    ],
    replayable: false,
    reconstructed: {
      contextSnapshot: {
        instances: Array.from({ length: 12 }, (_, index) => ({
          instanceId: `i-wide-${index + 1}`,
          state: 'running',
          tags: {
            env: 'dev',
            critical: 'false'
          }
        }))
      },
      blastRadius: {
        resourceCount: 12,
        resourceIds: Array.from({ length: 12 }, (_, index) => `i-wide-${index + 1}`),
        riskLevel: 'HIGH',
        reasons: [
          'Intent targets 12 resources',
          'Blast radius evaluated as HIGH (12 resources)'
        ]
      },
      evaluationResult: {
        allowed: true,
        reasons: [
          'No critical instances detected',
          'Intent targets 12 resources',
          'Blast radius evaluated as HIGH (12 resources)',
          'High-risk mutation allowed with warning'
        ]
      },
      executionResult: {
        success: true,
        result: {
          terminated: Array.from({ length: 12 }, (_, index) => `i-wide-${index + 1}`)
        }
      },
      finalOutcome: 'allowed'
    },
    steps: [],
    integrity: {
      valid: false,
      brokenAtEventId: 'critical-4'
    },
    events: [
      {
        id: 'critical-1',
        type: 'IntentReceived',
        timestamp: baseTimestamp + 20_000,
        intentId: 'critical-blast-demo',
        sequence: 1,
        previousEventHash: null,
        currentHash: eventHash(21),
        payload: {
          intentSnapshot: {
            id: 'critical-blast-demo',
            type: 'ec2:TerminateInstances',
            payload: Array.from({ length: 12 }, (_, index) => `i-wide-${index + 1}`),
            metadata: {
              actor: 'agent-3',
              timestamp: baseTimestamp + 20_000
            }
          },
          reasoningTrail: ['Intent submitted by actor=agent-3']
        }
      },
      {
        id: 'critical-2',
        type: 'BlastRadiusEvaluated',
        timestamp: baseTimestamp + 21_200,
        intentId: 'critical-blast-demo',
        sequence: 2,
        previousEventHash: eventHash(21),
        currentHash: eventHash(22),
        payload: {
          intentSnapshot: {
            id: 'critical-blast-demo',
            type: 'ec2:TerminateInstances',
            payload: Array.from({ length: 12 }, (_, index) => `i-wide-${index + 1}`),
            metadata: {
              actor: 'agent-3',
              timestamp: baseTimestamp + 20_000
            }
          },
          blastRadius: {
            resourceCount: 12,
            resourceIds: Array.from({ length: 12 }, (_, index) => `i-wide-${index + 1}`),
            riskLevel: 'HIGH',
            reasons: [
              'Intent targets 12 resources',
              'Blast radius evaluated as HIGH (12 resources)'
            ]
          },
          reasoningTrail: [
            'Intent targets 12 resources',
            'Blast radius evaluated as HIGH (12 resources)'
          ]
        }
      },
      {
        id: 'critical-3',
        type: 'EvaluationCompleted',
        timestamp: baseTimestamp + 22_000,
        intentId: 'critical-blast-demo',
        sequence: 3,
        previousEventHash: eventHash(22),
        currentHash: eventHash(23),
        payload: {
          intentSnapshot: {
            id: 'critical-blast-demo',
            type: 'ec2:TerminateInstances',
            payload: Array.from({ length: 12 }, (_, index) => `i-wide-${index + 1}`),
            metadata: {
              actor: 'agent-3',
              timestamp: baseTimestamp + 20_000
            }
          },
          evaluationResult: {
            allowed: true,
            reasons: [
              'No critical instances detected',
              'Intent targets 12 resources',
              'Blast radius evaluated as HIGH (12 resources)',
              'High-risk mutation allowed with warning'
            ]
          },
          reasoningTrail: [
            'High-risk mutation allowed with warning',
            'Intent approved for execution'
          ]
        }
      },
      {
        id: 'critical-4',
        type: 'ExecutionCompleted',
        timestamp: baseTimestamp + 23_000,
        intentId: 'critical-blast-demo',
        sequence: 4,
        previousEventHash: 'tampered-hash',
        currentHash: eventHash(24),
        payload: {
          intentSnapshot: {
            id: 'critical-blast-demo',
            type: 'ec2:TerminateInstances',
            payload: Array.from({ length: 12 }, (_, index) => `i-wide-${index + 1}`),
            metadata: {
              actor: 'agent-3',
              timestamp: baseTimestamp + 20_000
            }
          },
          executionResult: {
            success: true,
            result: {
              terminated: Array.from({ length: 12 }, (_, index) => `i-wide-${index + 1}`)
            }
          },
          reasoningTrail: ['Executor completed successfully']
        }
      }
    ]
  }
}

export const mockReplays: Record<string, ReplayResult> = {
  'allowed-demo': buildAllowedReplay(),
  'blocked-demo': buildBlockedReplay(),
  'critical-blast-demo': buildCriticalBlastReplay()
}
