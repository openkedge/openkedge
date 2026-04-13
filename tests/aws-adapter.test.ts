import {
  DescribeInstancesCommand,
  TerminateInstancesCommand
} from '@aws-sdk/client-ec2'

import { AwsContextProvider } from '../src/adapters/aws/AwsContextProvider'
import { AwsExecutor } from '../src/adapters/aws/AwsExecutor'
import { AwsSafetyPolicyEvaluator } from '../src/adapters/aws/AwsSafetyPolicyEvaluator'
import { OpenKedgeEngine } from '../src/core/engine/OpenKedgeEngine'
import { InMemoryEventStore } from '../src/core/event/InMemoryEventStore'
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
  })

  await expect(executor.execute(terminateIntent, {})).resolves.toEqual({
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
    async send() {
      throw new Error('should not be called')
    }
  })

  await expect(
    executor.execute(
      {
        ...terminateIntent,
        type: 's3:DeleteBucket'
      },
      {}
    )
  ).resolves.toEqual({
    success: false,
    error: 'Unsupported intent type: s3:DeleteBucket'
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
        async send() {
          throw new Error('execution should have been skipped')
        }
      }),
      store
    ),
    store
  )

  const result = await client.submitIntent(terminateIntent)
  const replay = await client.replayIntent(terminateIntent.id)

  expect(result.success).toBe(false)
  expect(replay.reconstructed.finalOutcome).toBe('blocked')
  expect(replay.events.at(-1)?.type).toBe(EventType.ExecutionSkipped)
  expect(replay.reasoningTrail).toContain(
    'Blocked termination of critical instances: i-123'
  )
  expect(replay.integrity.valid).toBe(true)
})
