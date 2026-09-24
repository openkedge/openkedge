import { resolve } from 'node:path'
import { FilePolicySource, writePolicyAtomically } from './policy'

async function main(): Promise<void> {
  const [file, command] = process.argv.slice(2)
  if (!file || !['status', 'deny-all', 'allow-dev'].includes(command)) {
    throw new Error('Usage: node dist/gateway/policy-cli.js POLICY_FILE status|deny-all|allow-dev')
  }
  const path = resolve(file)
  const source = new FilePolicySource(path)
  const { policy, revision } = await source.current()
  if (command === 'status') { console.log(revision); return }
  const version = /^v(\d+)$/.exec(policy.version)
  const next = { ...policy, version: version ? `v${Number(version[1]) + 1}` : `${policy.version}-next`,
    allowedInstanceIds: command === 'deny-all' ? [] : ['i-aaaaaaaaaaaaaaaaa'] }
  await writePolicyAtomically(path, next)
  console.log((await source.current()).revision)
}

main().catch(error => { console.error(error); process.exitCode = 1 })
