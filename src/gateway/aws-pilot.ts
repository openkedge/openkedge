import { readFile } from 'node:fs/promises'
import { EC2Client, DescribeInstancesCommand, TerminateInstancesCommand } from '@aws-sdk/client-ec2'
import { GetCallerIdentityCommand, STSClient } from '@aws-sdk/client-sts'
import type { Intent } from '../interfaces/contracts'
import type { ExecutionContract } from '../core/governance/types'
import type { ExecutionIdentity } from '../core/identity/Identity'
import { AwsIdentityProvider } from '../adapters/aws/AwsIdentityProvider'
import { AwsContextProvider } from '../adapters/aws/AwsContextProvider'
import type { GatewayContextResolver, TerminateParameters, TerminationAdapter } from './Gateway'
import type { PolicySource } from './policy'

export interface AwsPilotConfig {
  testAccountId: string
  region: string
  instanceId: string
  gatewayPrincipalArn: string
  agentPrincipalArn: string
  executionRoleArn: string
  requiredTag: { key: string; value: string }
  mutationEnabled: boolean
}

export function validateAwsPilotConfig(input: unknown): AwsPilotConfig {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('AWS_PILOT_CONFIG_INVALID')
  const v = input as Record<string, unknown>
  if (Object.keys(v).some(k => !['testAccountId', 'region', 'instanceId', 'gatewayPrincipalArn', 'agentPrincipalArn', 'executionRoleArn', 'requiredTag', 'mutationEnabled'].includes(k)) ||
    typeof v.testAccountId !== 'string' || !/^\d{12}$/.test(v.testAccountId) ||
    typeof v.region !== 'string' || !/^[a-z]{2}-[a-z]+-\d$/.test(v.region) ||
    typeof v.instanceId !== 'string' || !/^i-[a-f0-9]{17}$/.test(v.instanceId) ||
    typeof v.gatewayPrincipalArn !== 'string' || !/^arn:aws:(iam|sts)::\d{12}:(user|role|assumed-role)\/[A-Za-z0-9+=,.@_\/-]+$/.test(v.gatewayPrincipalArn) ||
    typeof v.agentPrincipalArn !== 'string' || !/^arn:aws:(iam|sts)::\d{12}:(user|role|assumed-role)\/[A-Za-z0-9+=,.@_\/-]+$/.test(v.agentPrincipalArn) ||
    typeof v.executionRoleArn !== 'string' || v.executionRoleArn !== `arn:aws:iam::${v.testAccountId}:role/OpenKedgePilotExecution` ||
    !v.requiredTag || typeof v.requiredTag !== 'object' || Array.isArray(v.requiredTag) ||
    Object.keys(v.requiredTag).some(k => !['key', 'value'].includes(k)) ||
    (v.requiredTag as Record<string, unknown>).key !== 'OpenKedgePilot' ||
    (v.requiredTag as Record<string, unknown>).value !== 'disposable' ||
    typeof v.mutationEnabled !== 'boolean') throw new Error('AWS_PILOT_CONFIG_INVALID')
  if (!v.gatewayPrincipalArn.includes(`::${v.testAccountId}:`) || !v.agentPrincipalArn.includes(`::${v.testAccountId}:`) ||
    v.gatewayPrincipalArn === v.agentPrincipalArn) throw new Error('AWS_PILOT_ACCOUNT_MISMATCH')
  return v as unknown as AwsPilotConfig
}

export async function loadAwsPilotConfig(path: string): Promise<AwsPilotConfig> {
  return validateAwsPilotConfig(JSON.parse(await readFile(path, 'utf8')) as unknown)
}

export interface AwsPilotClients {
  sts: Pick<STSClient, 'send'>
  ec2: Pick<EC2Client, 'send'>
  assumedEc2?: (identity: ExecutionIdentity) => Pick<EC2Client, 'send'>
}

export class AwsPilotError extends Error {
  readonly definitive = true
  constructor(readonly code: string, message: string) { super(`${code}: ${message}`) }
}

function awsCode(error: unknown): string {
  return error && typeof error === 'object' && 'name' in error ? String(error.name) : ''
}

export class AwsPilot implements GatewayContextResolver, TerminationAdapter {
  readonly identityProvider: AwsIdentityProvider
  private readonly sts: STSClient
  private readonly ec2: EC2Client

  constructor(readonly config: AwsPilotConfig, private readonly policy: PolicySource,
    private readonly mutateOptIn: boolean, private readonly clients?: AwsPilotClients) {
    if (mutateOptIn && !config.mutationEnabled) throw new AwsPilotError('AWS_PILOT_MUTATION_DISABLED', 'Test-account config has not enabled mutation')
    this.sts = new STSClient({ region: config.region, maxAttempts: 1 })
    this.ec2 = new EC2Client({ region: config.region, maxAttempts: 1 })
    this.identityProvider = new AwsIdentityProvider({ roleArn: config.executionRoleArn, region: config.region,
      requiredResourceTag: config.requiredTag, stsClient: (clients?.sts ?? this.sts) as STSClient })
  }

  async assertGatewayPrincipal(): Promise<void> {
    const reply = await (this.clients?.sts ?? this.sts).send(new GetCallerIdentityCommand({}))
    if (reply.Account !== this.config.testAccountId || reply.Arn !== this.config.gatewayPrincipalArn) {
      throw new AwsPilotError('AWS_PILOT_PRINCIPAL_MISMATCH', 'Gateway credentials are not the configured test-account principal')
    }
  }

  async assertPolicy(): Promise<void> {
    const { policy } = await this.policy.current()
    if (policy.allowedInstanceIds.length !== 1 || policy.allowedInstanceIds[0] !== this.config.instanceId ||
      policy.protectedInstanceIds.includes(this.config.instanceId) || policy.allowSkipOsShutdown) {
      throw new AwsPilotError('AWS_PILOT_POLICY_MISMATCH', 'Policy must allow only the configured target with skipOsShutdown disabled')
    }
  }

  async resolve(intent: Intent): Promise<{ instances: Array<{ instanceId?: string; state?: string; tags: Record<string, string | undefined> }> }> {
    await this.assertPolicy()
    await this.assertGatewayPrincipal()
    const payload = intent.payload as { instanceIds?: string[] }
    if (payload.instanceIds?.length !== 1 || payload.instanceIds[0] !== this.config.instanceId) {
      throw new AwsPilotError('AWS_PILOT_TARGET_MISMATCH', 'Target differs from test-account configuration')
    }
    const context = await new AwsContextProvider((this.clients?.ec2 ?? this.ec2) as EC2Client).resolve(intent)
    if (!('instances' in context) || context.lookupError || context.instances.length !== 1 ||
      context.instances[0].instanceId !== this.config.instanceId || context.instances[0].state !== 'running' ||
      context.instances[0].tags[this.config.requiredTag.key] !== this.config.requiredTag.value) {
      throw new AwsPilotError('AWS_PILOT_LIVE_GUARD_FAILED', 'Instance lookup, running state, or required disposable tag failed')
    }
    return { instances: context.instances }
  }

  private async guardBeforeAwsCall(intent: Intent): Promise<void> {
    try { await this.assertPolicy(); await this.resolve(intent) }
    catch (error) {
      if (error instanceof AwsPilotError) throw error
      throw new AwsPilotError('AWS_PILOT_PRECHECK_FAILED', 'Policy or live resource precheck unavailable')
    }
  }

  async terminate(params: TerminateParameters, identity?: ExecutionIdentity, intent?: Intent, grant?: ExecutionContract): Promise<unknown> {
    if (!identity?.accessKeyId || !identity.secretAccessKey || !identity.sessionToken || !intent || !grant ||
      identity.metadata?.roleArn !== this.config.executionRoleArn || identity.intentId !== intent.id ||
      identity.metadata?.assumedRoleArn !== `arn:aws:sts::${this.config.testAccountId}:assumed-role/OpenKedgePilotExecution/openkedge-${intent.id}`) {
      throw new AwsPilotError('AWS_PILOT_IDENTITY_MISSING', 'Restricted STS session is required')
    }
    if (params.instanceId !== this.config.instanceId || params.skipOsShutdown || grant.proposalId !== intent.id ||
      grant.actorId !== intent.metadata.actor || grant.action !== 'ec2:TerminateInstances') {
      throw new AwsPilotError('AWS_PILOT_CONTRACT_MISMATCH', 'Exact target, caller, grant, or parameters differ')
    }
    await this.guardBeforeAwsCall(intent)
    const assumed = this.clients?.assumedEc2?.(identity) ?? new EC2Client({ region: this.config.region, maxAttempts: 1,
      credentials: { accessKeyId: identity.accessKeyId, secretAccessKey: identity.secretAccessKey, sessionToken: identity.sessionToken } })
    const request = { InstanceIds: [params.instanceId], SkipOsShutdown: false }
    let dryRunRequestId: string | undefined
    try {
      await assumed.send(new TerminateInstancesCommand({ ...request, DryRun: true }))
      throw new AwsPilotError('AWS_DRY_RUN_UNEXPECTED', 'DryRun returned success without DryRunOperation')
    } catch (error) {
      if (error instanceof AwsPilotError) throw error
      if (awsCode(error) !== 'DryRunOperation') {
        throw new AwsPilotError('AWS_DRY_RUN_DENIED', `DryRun did not authorize the request (${awsCode(error) || 'unknown error'})`)
      }
      dryRunRequestId = error && typeof error === 'object' && '$metadata' in error
        ? (error.$metadata as { requestId?: string })?.requestId : undefined
    }
    const correlation = { proposalId: intent.id, contractId: grant.contractId,
      assumedRoleArn: identity.metadata?.assumedRoleArn, region: this.config.region, instanceId: params.instanceId,
      dryRunRequestId }
    if (!this.mutateOptIn) return { mode: 'dry-run', dryRunAuthorized: true, ...correlation }
    // Repeat the live guard after DryRun, directly before the only mutating call.
    await this.guardBeforeAwsCall(intent)
    const response = await assumed.send(new TerminateInstancesCommand({ ...request, DryRun: false }))
    if (!response.$metadata?.requestId) throw new Error('AWS_OUTCOME_UNCERTAIN: EC2 response lacked request ID')
    return { mode: 'mutation', apiAccepted: true, ec2RequestId: response.$metadata.requestId,
      currentState: response.TerminatingInstances?.[0]?.CurrentState?.Name, ...correlation }
  }
}
