import { generateKeyPairSync } from 'node:crypto'
import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ControllerPolicyClient } from '../src/control-plane/client'
import { signBundle, validatePermitRequest, verifyBundle } from '../src/control-plane/protocol'
import type { GatewayPolicy } from '../src/gateway/policy'

const policy: GatewayPolicy = { protocolVersion: 1, version: 'v1', allowedInstanceIds: ['i-aaaaaaaaaaaaaaaaa'],
  protectedInstanceIds: [], allowSkipOsShutdown: false, instances: {}, rules: [] }
const keys = generateKeyPairSync('ed25519')
const privateKey = keys.privateKey.export({ type: 'pkcs8', format: 'pem' }).toString()
const publicKey = keys.publicKey.export({ type: 'spki', format: 'pem' }).toString()

test('signed bundle rejects unsigned, malformed and tampered content', () => {
  const bundle = signBundle(policy, 1, privateKey)
  expect(verifyBundle(bundle, publicKey).bundle.epoch).toBe(1)
  const { signature: _signature, ...unsigned } = bundle
  expect(() => verifyBundle(unsigned, publicKey)).toThrow('INVALID_POLICY_BUNDLE')
  expect(() => verifyBundle({ ...bundle, policy: { ...policy, version: 'tampered' } }, publicKey)).toThrow('POLICY_SIGNATURE_INVALID')
  expect(() => verifyBundle({ ...bundle, epoch: '1' }, publicKey)).toThrow('INVALID_POLICY_BUNDLE')
  expect(() => signBundle({ ...policy, rules: [{ type: 'RATE_LIMIT', targetAction: 'ec2:TerminateInstances',
    windowMs: 1, maxCount: -1 }] }, 2, privateKey)).toThrow('POLICY_UNAVAILABLE')
  expect(() => validatePermitRequest({ protocolVersion: 1, proposalId: 'x', contractId: 'x', actorId: 'x',
    action: 'ec2:TerminateInstances', policyVersion: 'x', instanceId: 'i-aaaaaaaaaaaaaaaaa', skipOsShutdown: false, grantNotAfter: 2,
    approved: true })).toThrow('INVALID_PERMIT_REQUEST')
})

test('gateway persists monotonic epoch and rejects rollback after restart', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'okg-control-test-'))
  const path = join(dir, 'cache.json')
  const later = signBundle(policy, 2, privateKey)
  const earlier = signBundle(policy, 1, privateKey)
  const original = global.fetch
  let served = later
  global.fetch = jest.fn(async (_url, options) => ({ ok: true, status: 200,
    json: async () => JSON.parse(JSON.stringify(options?.method === 'POST' ? { accepted: true } : served)) })) as unknown as typeof fetch
  try {
    const config = { url: 'http://127.0.0.1:12345', gatewayId: 'one', token: 'a'.repeat(32), publicKey, statePath: path }
    const first = new ControllerPolicyClient(config)
    expect((await first.current()).epoch).toBe(2)
    expect(JSON.parse(await readFile(path, 'utf8')).epoch).toBe(2)
    served = earlier
    const restarted = new ControllerPolicyClient(config)
    await expect(restarted.current()).rejects.toThrow('POLICY_ROLLBACK_REJECTED')
    served = signBundle(policy, 2, privateKey)
    await expect(restarted.current()).rejects.toThrow('POLICY_ROLLBACK_REJECTED')
  } finally { global.fetch = original }
})

test('bounded lease survives outage only until expiry; consequential action needs controller', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'okg-control-test-'))
  const bundle = signBundle(policy, 1, privateKey, Date.now(), 40)
  const original = global.fetch
  let online = true
  global.fetch = jest.fn(async (_url, options) => {
    if (!online) throw new Error('offline')
    return { ok: true, status: 200,
      json: async () => JSON.parse(JSON.stringify(options?.method === 'POST' ? { accepted: true } : bundle)) } as Response
  }) as typeof fetch
  try {
    const client = new ControllerPolicyClient({ url: 'http://127.0.0.1:12345', gatewayId: 'one',
      token: 'a'.repeat(32), publicKey, statePath: join(dir, 'cache.json') })
    await client.current()
    online = false
    expect((await client.currentFor('bounded')).epoch).toBe(1)
    await expect(client.currentFor('consequential')).rejects.toThrow('CONTROLLER_UNAVAILABLE')
    await new Promise(resolve => setTimeout(resolve, 45))
    await expect(client.currentFor('bounded')).rejects.toThrow('CONTROLLER_UNAVAILABLE')
  } finally { global.fetch = original }
})

test('controller client refuses non-loopback plaintext transport', () => {
  expect(() => new ControllerPolicyClient({ url: 'http://controller.example', gatewayId: 'one',
    token: 'a'.repeat(32), publicKey, statePath: '/tmp/unused' })).toThrow('CONTROLLER_TLS_REQUIRED')
})

test('unauthorized distribution response is rejected', async () => {
  const original = global.fetch
  global.fetch = jest.fn(async () => ({ ok: false, status: 401,
    json: async () => ({ code: 'UNAUTHORIZED' }) })) as unknown as typeof fetch
  try {
    const dir = await mkdtemp(join(tmpdir(), 'okg-control-test-'))
    const client = new ControllerPolicyClient({ url: 'http://127.0.0.1:12345', gatewayId: 'one',
      token: 'a'.repeat(32), publicKey, statePath: join(dir, 'cache.json') })
    await expect(client.current()).rejects.toThrow('CONTROLLER_UNAUTHORIZED')
  } finally { global.fetch = original }
})
