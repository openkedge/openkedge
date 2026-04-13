import {
  DescribeInstancesCommand,
  TerminateInstancesCommand
} from '@aws-sdk/client-ec2'
import type { AssumeRoleCommandOutput } from '@aws-sdk/client-sts'

import { AwsContextProvider } from '../src/adapters/aws/AwsContextProvider'
import { AwsExecutor } from '../src/adapters/aws/AwsExecutor'
import {
  AwsIdentityProvider,
  generatePolicy
} from '../src/adapters/aws/AwsIdentityProvider'
import { AwsSafetyPolicyEvaluator } from '../src/adapters/aws/AwsSafetyPolicyEvaluator'
import { OpenKedgeEngine } from '../src/core/engine/OpenKedgeEngine'
import { InMemoryEventStore } from '../src/core/event/InMemoryEventStore'
import { IdentityManager } from '../src/core/identity/IdentityManager'
import { OpenKedgeClient } from '../src/sdk/client'
import type { Intent } from '../src/interfaces/contracts'
import { EventType } from '../src/interfaces/contracts'

const terminateIntent: Intent = {
  id: 'intent-aws-1',
  type: 'ec2:TerminateInstances',
  payload: ['i-123'],
  metadata: {
    actor: 'tester',
    timestamp: 1
  }
}

test('AwsContextProvider resolves instance metadata for termination intents', async () => {
  const provider = new AwsContextProvider({
    async send(command) {
      expect(command).toBeInstanceOf(DescribeInstancesCommand)

      return {
        Reservations: [
          {
            Instances: [
              {
                InstanceId: 'i-123',
                State: { Name: 'running' },
                Tags: [
                  { Key: 'critical', Value: 'true' },
                  { Key: 'env', Value: 'prod' }
                ]
              }
            ]
          }
        ]
      }
    }
  })

  await expect(provider.resolve(terminateIntent)).resolves.toEqual({
    instances: [
      {
        instanceId: 'i-123',
        state: 'running',
        tags: {
          critical: 'true',
          env: 'prod'
        }
      }
    ]
  })
})

test('AwsSafetyPolicyEvaluator blocks critical instances from termination', async () => {
  const evaluator = new AwsSafetyPolicyEvaluator()

  await expect(
    evaluator.evaluate(terminateIntent, {
      instances: [
        {
          instanceId: 'i-123',
          state: 'running',
          tags: { critical: 'true' }
        }
      ]
    })
  ).resolves.toEqual({
    allowed: false,
    reasons: ['Blocked termination of critical instances: i-123'],
    enrichedContext: {
      instances: [
        {
          instanceId: 'i-123',
          state: 'running',
          tags: { critical: 'true' }
        }
      ]
    }
  })
})

test('AwsExecutor terminates requested instances', async () => {
  const executor = new AwsExecutor({
    clientFactory(identity) {
      expect(identity.accessKeyId).toBe('ASIATESTACCESS1234')

      return {
        async send(command) {
          expect(command).toBeInstanceOf(TerminateInstancesCommand)

          return {
            $metadata: {},
            TerminatingInstances: [
              {
                InstanceId: 'i-123'
              }
            ]
          }
        }
      }
    }
  })

  await expect(
    executor.execute(terminateIntent, {}, {
      id: 'identity-1',
      intentId: terminateIntent.id,
      issuedAt: Date.now(),
      expiresAt: Date.now() + 60_000,
      permissions: ['ec2:TerminateInstances'],
      accessKeyId: 'ASIATESTACCESS1234',
      secretAccessKey: 'secret',
      sessionToken: 'token',
      metadata: {
        allowedInstanceIds: ['i-123']
      }
    })
  ).resolves.toEqual({
    success: true,
    result: {
      $metadata: {},
      TerminatingInstances: [
        {
          InstanceId: 'i-123'
        }
      ]
    }
  })
})

test('AwsExecutor rejects unsupported intent types', async () => {
  const executor = new AwsExecutor({
    clientFactory() {
      return {
        async send() {
          throw new Error('should not be called')
        }
      }
    }
  })

  await expect(
    executor.execute(
      {
        ...terminateIntent,
        type: 's3:DeleteBucket'
      },
      {},
      {
        id: 'identity-unsupported',
        intentId: terminateIntent.id,
        issuedAt: Date.now(),
        expiresAt: Date.now() + 60_000,
        permissions: ['s3:DeleteBucket'],
        metadata: {}
      }
    )
  ).resolves.toEqual({
    success: false,
    error: 'Unsupported intent type: s3:DeleteBucket'
  })
})

test('AwsExecutor rejects resource scope mismatches', async () => {
  const executor = new AwsExecutor({
    clientFactory() {
      return {
        async send() {
          throw new Error('should not be called')
        }
      }
    }
  })

  await expect(
    executor.execute(terminateIntent, {}, {
      id: 'identity-scope',
      intentId: terminateIntent.id,
      issuedAt: Date.now(),
      expiresAt: Date.now() + 60_000,
      permissions: ['ec2:TerminateInstances'],
      accessKeyId: 'ASIATESTACCESS1234',
      secretAccessKey: 'secret',
      sessionToken: 'token',
      metadata: {
        allowedInstanceIds: ['i-999']
      }
    })
  ).rejects.toThrow(
    'Execution identity identity-scope does not cover instance IDs: i-123'
  )
})

test('AwsIdentityProvider issues scoped STS identities', async () => {
  const provider = new AwsIdentityProvider({
    roleArn: 'arn:aws:iam::123456789012:role/OpenKedgeExecutionRole',
    region: 'us-east-1',
    stsClient: {
      async send(command): Promise<AssumeRoleCommandOutput> {
        expect(command.input.RoleSessionName).toBe(`openkedge-${terminateIntent.id}`)
        expect(command.input.DurationSeconds).toBe(900)
        expect(command.input.Policy).toContain(
          'arn:aws:ec2:us-east-1:123456789012:instance/i-123'
        )

        return {
          $metadata: {},
          Credentials: {
            AccessKeyId: 'ASIATESTACCESS1234',
            SecretAccessKey: 'secret',
            SessionToken: 'token',
            Expiration: new Date(Date.now() + 15 * 60 * 1000)
          }
        }
      }
    }
  })

  const identity = await provider.issueIdentity(terminateIntent)

  expect(identity.permissions).toEqual(['ec2:TerminateInstances'])
  expect(identity.metadata).toMatchObject({
    provider: 'aws-sts',
    roleArn: 'arn:aws:iam::123456789012:role/OpenKedgeExecutionRole',
    region: 'us-east-1',
    accessKeyIdMasked: 'ASIA****1234',
    allowedInstanceIds: ['i-123']
  })
})

test('generatePolicy scopes terminate intent to the target instance ARN', () => {
  expect(
    generatePolicy(
      terminateIntent,
      'arn:aws:iam::123456789012:role/OpenKedgeExecutionRole',
      'us-east-1'
    )
  ).toEqual({
    Version: '2012-10-17',
    Statement: [
      {
        Effect: 'Allow',
        Action: ['ec2:TerminateInstances'],
        Resource: ['arn:aws:ec2:us-east-1:123456789012:instance/i-123']
      }
    ]
  })
})

test('AWS adapter decisions are replayable through the evidence chain', async () => {
  const store = new InMemoryEventStore()
  const client = new OpenKedgeClient(
    new OpenKedgeEngine(
      new AwsContextProvider({
        async send() {
          return {
            Reservations: [
              {
                Instances: [
                  {
                    InstanceId: 'i-123',
                    State: { Name: 'running' },
                    Tags: [{ Key: 'critical', Value: 'true' }]
                  }
                ]
              }
            ]
          }
        }
      }),
      new AwsSafetyPolicyEvaluator(),
      new AwsExecutor({
        clientFactory() {
          return {
            async send() {
              throw new Error('execution should have been skipped')
            }
          }
        }
      }),
      new IdentityManager(
        {
          async issueIdentity(intent) {
            const issuedAt = Date.now()

            return {
              id: `aws-${intent.id}-${issuedAt}`,
              intentId: intent.id,
              issuedAt,
              expiresAt: issuedAt + 60_000,
              permissions: [intent.type],
              metadata: {
                allowedInstanceIds: ['i-123']
              }
            }
          },
          async revokeIdentity(identity) {
            identity.metadata = {
              ...identity.metadata,
              revokedAt: Date.now()
            }
          }
        },
        store
      ),
      store
    ),
    store
  )

  const result = await client.submitIntent(terminateIntent)
  const replay = await client.replayIntent(terminateIntent.id)

  expect(result.success).toBe(false)
  expect(replay.reconstructed.finalOutcome).toBe('blocked')
  expect(replay.reconstructed.blastRadius?.riskLevel).toBe('CRITICAL')
  expect(replay.events.map((event) => event.type)).toContain(
    EventType.BlastRadiusEvaluated
  )
  expect(replay.events.at(-1)?.type).toBe(EventType.ExecutionSkipped)
  expect(replay.reasoningTrail).toContain(
    'Blocked termination of critical instances: i-123'
  )
  expect(replay.integrity.valid).toBe(true)
})
