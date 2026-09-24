import { AssumeRoleCommand, GetCallerIdentityCommand } from '@aws-sdk/client-sts'
import { DescribeInstancesCommand, TerminateInstancesCommand } from '@aws-sdk/client-ec2'
import { AwsPilot, validateAwsPilotConfig } from '../src/gateway/aws-pilot'
import { ExecutionGateway } from '../src/gateway/Gateway'
import { validatePolicy, type GatewayPolicy, type PolicySource } from '../src/gateway/policy'
import { EventType } from '../src/interfaces/contracts'

const instanceId = 'i-aaaaaaaaaaaaaaaaa'
const config = validateAwsPilotConfig({ testAccountId: '123456789012', region: 'us-east-1', instanceId,
  gatewayPrincipalArn: 'arn:aws:iam::123456789012:user/pilot-gateway',
  agentPrincipalArn: 'arn:aws:iam::123456789012:user/pilot-agent',
  executionRoleArn: 'arn:aws:iam::123456789012:role/OpenKedgePilotExecution',
  requiredTag: { key: 'OpenKedgePilot', value: 'disposable' }, mutationEnabled: true })
const params = { instanceId, skipOsShutdown: false }

function fixture(mutate = false) {
  let tag = 'disposable'
  let state = 'running'
  let dryRunAuthorized = true
  let afterDryRun: (() => void) | undefined
  let clockOffsetMs = 0
  let mutationError: Error | undefined
  let policy: GatewayPolicy = { protocolVersion: 1, version: 'v1', allowedInstanceIds: [instanceId],
    protectedInstanceIds: [], allowSkipOsShutdown: false, instances: {}, rules: [] }
  const source: PolicySource = { current: async () => validatePolicy(policy) }
  const calls: Array<{ dryRun: boolean; instanceIds: string[] }> = []
  const sts = { send: async (command: unknown) => {
    if (command instanceof GetCallerIdentityCommand) return { Account: config.testAccountId, Arn: config.gatewayPrincipalArn }
    if (command instanceof AssumeRoleCommand) {
      const sessionPolicy = JSON.parse(command.input.Policy ?? '{}') as { Statement: Array<{Resource: string[]; Condition: {StringEquals: Record<string,string>}}> }
      expect(sessionPolicy.Statement[0].Resource).toEqual([`arn:aws:ec2:us-east-1:123456789012:instance/${instanceId}`])
      expect(sessionPolicy.Statement[0].Condition.StringEquals['aws:ResourceTag/OpenKedgePilot']).toBe('disposable')
      return { Credentials: { AccessKeyId: 'ASIATEST', SecretAccessKey: 'test-secret',
      SessionToken: 'test-token', Expiration: new Date(Date.now() + 900_000) },
      AssumedRoleUser: { Arn: `arn:aws:sts::123456789012:assumed-role/OpenKedgePilotExecution/${command.input.RoleSessionName}` } }
    }
    throw new Error('Unknown STS command')
  } }
  const ec2 = { send: async (command: unknown) => {
    if (command instanceof DescribeInstancesCommand) return { Reservations: [{ Instances: [{ InstanceId: instanceId,
      State: { Name: state }, Tags: [{ Key: 'OpenKedgePilot', Value: tag }] }] }] }
    if (command instanceof TerminateInstancesCommand) {
      calls.push({ dryRun: Boolean(command.input.DryRun), instanceIds: command.input.InstanceIds ?? [] })
      if (command.input.DryRun) { afterDryRun?.(); const error = new Error(dryRunAuthorized ? 'DryRunOperation' : 'UnauthorizedOperation');
        error.name = dryRunAuthorized ? 'DryRunOperation' : 'UnauthorizedOperation'; throw error }
      if (mutationError) throw mutationError
      return { $metadata: { requestId: 'ec2-request-test' }, TerminatingInstances: [{ CurrentState: { Name: 'shutting-down' } }] }
    }
    throw new Error('Unknown EC2 command')
  } }
  const pilot = new AwsPilot(config, source, mutate, { sts: sts as never, ec2: ec2 as never,
    assumedEc2: () => ec2 as never })
  const gateway = new ExecutionGateway(source, pilot, '0123456789abcdef0123456789abcdef',
    { gatewayId: 'pilot', callerId: 'agent', delegatedBy: 'operator' }, undefined, () => Date.now() + clockOffsetMs,
    undefined, undefined, pilot.identityProvider, pilot)
  return { gateway, pilot, calls, setTag: (v: string) => { tag = v }, setState: (v: string) => { state = v },
    denyDryRun: () => { dryRunAuthorized = false }, onDryRun: (fn: () => void) => { afterDryRun = fn },
    setMutationError: (v: Error) => { mutationError = v },
    advanceClock: (ms: number) => { clockOffsetMs += ms },
    setPolicy: (v: GatewayPolicy) => { policy = v }, policy: () => policy }
}

test('pilot config requires explicit test account, exact role, and separate principals', () => {
  expect(() => validateAwsPilotConfig({ ...config, executionRoleArn: 'arn:aws:iam::123456789012:role/Admin' })).toThrow('AWS_PILOT_CONFIG_INVALID')
  expect(() => validateAwsPilotConfig({ ...config, agentPrincipalArn: config.gatewayPrincipalArn })).toThrow('AWS_PILOT_ACCOUNT_MISMATCH')
})

test('dry-run pilot uses restricted STS identity and records validation without mutation', async () => {
  const f = fixture()
  const decision = await f.gateway.admit(params)
  if (decision.status !== 'allowed') throw new Error('Expected grant')
  const result = await f.gateway.execute(decision.grant, params)
  expect(result.status).toBe('validated')
  expect(f.calls).toEqual([{ dryRun: true, instanceIds: [instanceId] }])
  const replay = await f.gateway.replay(decision.intentId)
  expect(replay.integrity.valid).toBe(true)
  expect(replay.events.find(e => e.type === EventType.ExecutionCompleted)?.payload.executionResult?.result).toMatchObject({ mode: 'dry-run', contractId: decision.grant.contractId })
  expect(replay.events.find(e => e.type === EventType.IdentityIssued)?.payload.identitySnapshot).not.toHaveProperty('secretAccessKey')
})

test('target substitution, changed live tag or state, and policy changes fail before AWS mutation', async () => {
  const f = fixture(true)
  const a = await f.gateway.admit(params)
  if (a.status !== 'allowed') throw new Error('Expected grant')
  expect((await f.gateway.execute(a.grant, { ...params, instanceId: 'i-bbbbbbbbbbbbbbbbb' })).code).toBe('OPERATION_MISMATCH')
  f.setTag('ordinary')
  expect((await f.gateway.execute(a.grant, params)).status).toBe('rejected')
  expect(f.calls).toHaveLength(0)
  f.setTag('disposable')
  f.setState('stopped')
  expect((await f.gateway.execute(a.grant, params)).status).toBe('rejected')
  f.setState('running')
  f.setPolicy({ ...f.policy(), version: 'v2' })
  expect((await f.gateway.execute(a.grant, params)).code).toBe('POLICY_VERSION_CONFLICT')
})

test('missing permission is definitive and consumes the grant; concurrent redemption calls AWS once', async () => {
  const f = fixture(true)
  const a = await f.gateway.admit(params)
  if (a.status !== 'allowed') throw new Error('Expected grant')
  f.denyDryRun()
  expect((await f.gateway.execute(a.grant, params)).status).toBe('failed')
  expect((await f.gateway.execute(a.grant, params)).code).toBe('GRANT_REPLAY')
  expect(f.calls).toHaveLength(1)
  const g = fixture(true)
  const b = await g.gateway.admit(params)
  if (b.status !== 'allowed') throw new Error('Expected grant')
  const both = await Promise.all([g.gateway.execute(b.grant, params), g.gateway.execute(b.grant, params)])
  expect(both.filter(r => r.status === 'executed')).toHaveLength(1)
  expect(g.calls.filter(c => !c.dryRun)).toHaveLength(1)
})

test('tag change between DryRun and real dispatch blocks mutation', async () => {
  const f = fixture(true)
  const decision = await f.gateway.admit(params)
  if (decision.status !== 'allowed') throw new Error('Expected grant')
  f.onDryRun(() => f.setTag('ordinary'))
  expect((await f.gateway.execute(decision.grant, params)).status).toBe('failed')
  expect(f.calls).toEqual([{ dryRun: true, instanceIds: [instanceId] }])
})

test('expired pilot grant never reaches AWS', async () => {
  const f = fixture(true)
  const decision = await f.gateway.admit(params)
  if (decision.status !== 'allowed') throw new Error('Expected grant')
  f.advanceClock(31_000)
  expect((await f.gateway.execute(decision.grant, params)).status).toBe('rejected')
  expect(f.calls).toHaveLength(0)
})

test('AWS timeout after dispatch remains uncertain and is never recorded as success', async () => {
  const f = fixture(true)
  const timeout = new Error('socket timeout'); timeout.name = 'TimeoutError'
  f.setMutationError(timeout)
  const decision = await f.gateway.admit(params)
  if (decision.status !== 'allowed') throw new Error('Expected grant')
  const result = await f.gateway.execute(decision.grant, params)
  expect(result.status).toBe('uncertain')
  expect(result.code).toBe('OUTCOME_UNCERTAIN')
  const replay = await f.gateway.replay(decision.intentId)
  expect(replay.events.find(e => e.type === EventType.ExecutionCompleted)?.payload.executionResult?.success).toBe(false)
  expect((await f.gateway.execute(decision.grant, params)).code).toBe('GRANT_REPLAY')
})
