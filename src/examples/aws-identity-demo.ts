import { randomUUID } from 'node:crypto'

import { TerminateInstancesCommand } from '@aws-sdk/client-ec2'
import type { AssumeRoleCommandOutput } from '@aws-sdk/client-sts'

import { AwsExecutor } from '../adapters/aws/AwsExecutor'
import { AwsIdentityProvider } from '../adapters/aws/AwsIdentityProvider'
import { OpenKedgeEngine } from '../core/engine/OpenKedgeEngine'
import { InMemoryEventStore } from '../core/event/InMemoryEventStore'
import { IdentityManager } from '../core/identity/IdentityManager'
import { AwsContextProvider } from '../adapters/aws/AwsContextProvider'
import { AwsSafetyPolicyEvaluator } from '../adapters/aws/AwsSafetyPolicyEvaluator'
import { OpenKedgeClient } from '../sdk/client'
import type { Intent } from '../interfaces/contracts'

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function createDemoIntent(intentId: string): Intent {
  return {
    id: intentId,
    type: 'ec2:TerminateInstances',
    payload: ['i-1234567890abcdef0'],
    metadata: {
      actor: 'aws-identity-demo',
      timestamp: Date.now()
    }
  }
}

async function runSuccessScenario(): Promise<void> {
  const store = new InMemoryEventStore()
  const intent = createDemoIntent(randomUUID())
  const identityProvider = new AwsIdentityProvider({
    roleArn: 'arn:aws:iam::123456789012:role/OpenKedgeExecutionRole',
    region: 'us-east-1',
    stsClient: {
      async send(): Promise<AssumeRoleCommandOutput> {
        return {
          $metadata: {},
          Credentials: {
            AccessKeyId: 'ASIADEMOACCESS1234',
            SecretAccessKey: 'demo-secret-key',
            SessionToken: 'demo-session-token',
            Expiration: new Date(Date.now() + 15 * 60 * 1000)
          }
        }
      }
    }
  })
  const engine = new OpenKedgeEngine(
    new AwsContextProvider({
      async send() {
        return {
          Reservations: [
            {
              Instances: [
                {
                  InstanceId: 'i-1234567890abcdef0',
                  State: { Name: 'running' },
                  Tags: [{ Key: 'critical', Value: 'false' }]
                }
              ]
            }
          ]
        }
      }
    }),
    new AwsSafetyPolicyEvaluator(),
    new AwsExecutor({
      region: 'us-east-1',
      clientFactory(identity) {
        return {
          async send(command) {
            if (!(command instanceof TerminateInstancesCommand)) {
              throw new Error('demo only supports TerminateInstances')
            }

            return {
              $metadata: {},
              identityId: identity.id,
              TerminatingInstances: [
                {
                  InstanceId: 'i-1234567890abcdef0'
                }
              ]
            }
          }
        }
      }
    }),
    new IdentityManager(identityProvider, store),
    store
  )
  const client = new OpenKedgeClient(engine, store)
  const result = await client.submitIntent(intent)
  const events = await client.getEventsByIntent(intent.id)

  console.log('SUCCESSFUL EXECUTION:')
  console.log(JSON.stringify(result, null, 2))
  console.log('EVENT TYPES:')
  console.log(events.map((event) => event.type).join(' -> '))
}

async function runExpiredIdentityScenario(): Promise<void> {
  const intent = createDemoIntent(randomUUID())
  const identityProvider = new AwsIdentityProvider({
    roleArn: 'arn:aws:iam::123456789012:role/OpenKedgeExecutionRole',
    region: 'us-east-1',
    ttlSeconds: 1,
    stsClient: {
      async send(): Promise<AssumeRoleCommandOutput> {
        return {
          $metadata: {},
          Credentials: {
            AccessKeyId: 'ASIAEXPIREDACCESS12',
            SecretAccessKey: 'expired-secret-key',
            SessionToken: 'expired-session-token',
            Expiration: new Date(Date.now() + 15 * 60 * 1000)
          }
        }
      }
    }
  })
  const manager = new IdentityManager(identityProvider, new InMemoryEventStore())
  const executor = new AwsExecutor({
    region: 'us-east-1',
    clientFactory() {
      return {
        async send() {
          return {
            $metadata: {}
          }
        }
      }
    }
  })

  try {
    await manager.withIdentity(intent, async (identity) => {
      await sleep(1_100)
      return executor.execute(intent, {}, identity)
    })
  } catch (error) {
    console.log('EXPIRED IDENTITY FAILURE:')
    console.log(error instanceof Error ? error.message : String(error))
  }
}

async function main(): Promise<void> {
  await runSuccessScenario()
  await runExpiredIdentityScenario()
}

void main()
