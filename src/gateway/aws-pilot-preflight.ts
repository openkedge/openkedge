import { resolve } from 'node:path'
import { EC2Client, TerminateInstancesCommand } from '@aws-sdk/client-ec2'
import { AssumeRoleCommand, GetCallerIdentityCommand, STSClient } from '@aws-sdk/client-sts'
import { loadAwsPilotConfig } from './aws-pilot'

async function main(): Promise<void> {
  const path = process.env.OKG_AWS_PILOT_CONFIG
  if (!path) throw new Error('OKG_AWS_PILOT_CONFIG is required')
  const config = await loadAwsPilotConfig(resolve(path))
  const sts = new STSClient({ region: config.region, maxAttempts: 1 })
  const ec2 = new EC2Client({ region: config.region, maxAttempts: 1 })
  const caller = await sts.send(new GetCallerIdentityCommand({}))
  if (caller.Account !== config.testAccountId || caller.Arn !== config.agentPrincipalArn) {
    throw new Error('AGENT_PRINCIPAL_MISMATCH: Use the explicitly configured test-account agent profile')
  }
  let directDenied = false
  try {
    await ec2.send(new TerminateInstancesCommand({ InstanceIds: [config.instanceId], SkipOsShutdown: false, DryRun: true }))
  } catch (error) {
    directDenied = error instanceof Error && error.name === 'UnauthorizedOperation'
  }
  let assumeDenied = false
  try {
    await sts.send(new AssumeRoleCommand({ RoleArn: config.executionRoleArn,
      RoleSessionName: 'openkedge-preflight', DurationSeconds: 900 }))
  } catch (error) {
    assumeDenied = error instanceof Error && (error.name === 'AccessDenied' || error.name === 'AccessDeniedException')
  }
  if (!directDenied || !assumeDenied) throw new Error('DIRECT_AGENT_BYPASS_OR_INCONCLUSIVE: Direct DryRun and AssumeRole must both return explicit authorization denials')
  console.log(JSON.stringify({ account: caller.Account, agentPrincipalArn: caller.Arn,
    directTerminate: 'denied', assumeExecutionRole: 'denied' }))
}

main().catch(error => { console.error(error instanceof Error ? error.message : 'PREFLIGHT_FAILED'); process.exitCode = 1 })
