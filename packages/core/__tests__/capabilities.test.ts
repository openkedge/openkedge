import { mintCapabilityToken, verifyCapabilityToken } from '..'
import { InMemoryEventStore } from '../../../src/core/event/InMemoryEventStore'
import type { CapabilityToken } from '../../../src/core/governance/types'
import { intent, NOW, record, SECRET } from '../../../tests/helpers/temporal'

async function token() {
  const source = await record(new InMemoryEventStore(), intent('lookup', {}, { kind: 'READ' }), NOW - 1)
  return mintCapabilityToken(source, { resourceId: 'dev', targetAccountId: 'account', options: { a: 1, b: 2 } }, SECRET, 1_000, NOW)
}

test('mints canonical tokens and verifies all bound parameters', async () => {
  const capability = await token()
  expect(verifyCapabilityToken(capability, { resourceId: 'dev', targetAccountId: 'account', options: { b: 2, a: 1 } }, SECRET, NOW)).toEqual({ valid: true })
  expect(verifyCapabilityToken(capability, { resourceId: 'production' }, SECRET, NOW)).toMatchObject({ valid: false, code: 'CAPABILITY_MISMATCH_ERROR' })
  expect(capability.signature).toMatch(/^[0-9a-f]{64}$/)
})

test.each<keyof CapabilityToken>(['tokenId', 'sourceProposalId', 'sourceHash', 'actorId', 'issuedAt', 'expiresAt', 'boundAttributes', 'signature'])(
  'authenticates the %s field, not just the payload', async field => {
    const capability = await token()
    const mutations: CapabilityToken = { ...capability, tokenId: 'fake', sourceProposalId: 'fake', sourceHash: 'a'.repeat(64), actorId: 'other',
      issuedAt: NOW - 10, expiresAt: NOW + 2_000, boundAttributes: { resourceId: 'production' }, signature: 'b'.repeat(64) }
    const tampered = { ...capability, [field]: mutations[field] }
    expect(verifyCapabilityToken(tampered, { resourceId: 'production' }, SECRET, NOW).valid).toBe(false)
  }
)

test('expiry is exclusive and future-issued tokens are not active', async () => {
  const capability = await token()
  const payload = capability.boundAttributes
  expect(verifyCapabilityToken(capability, payload, SECRET, NOW + 999).valid).toBe(true)
  expect(verifyCapabilityToken(capability, payload, SECRET, NOW + 1_000)).toMatchObject({ code: 'CAPABILITY_EXPIRED_ERROR' })
  expect(verifyCapabilityToken(capability, payload, SECRET, NOW - 1).valid).toBe(false)
})

test('rejects malformed and non-JSON bindings without silently coercing them', async () => {
  const source = await record(new InMemoryEventStore(), intent('lookup', {}, { kind: 'READ' }), NOW)
  for (const attributes of [{}, { amount: NaN }, { amount: Infinity }, { missing: undefined }]) {
    expect(() => mintCapabilityToken(source, attributes, SECRET, 1_000, NOW)).toThrow()
  }
  expect(() => mintCapabilityToken({ ...source, status: 'FAILED' }, { resourceId: 'dev' }, SECRET, 1_000, NOW)).toThrow()
  expect(() => mintCapabilityToken({ ...source, kind: 'MUTATION' }, { resourceId: 'dev' }, SECRET, 1_000, NOW)).toThrow()
  expect(() => mintCapabilityToken(source, { resourceId: 'dev' }, 'weak', 1_000, NOW)).toThrow()
  expect(verifyCapabilityToken(null as unknown as CapabilityToken, {}, SECRET, NOW).valid).toBe(false)
})

test('snapshots bindings so caller mutations cannot alter the signed token', async () => {
  const source = await record(new InMemoryEventStore(), intent('lookup', {}, { kind: 'READ' }), NOW)
  const attributes = { nested: { id: 'dev' } }
  const capability = mintCapabilityToken(source, attributes, SECRET, 1_000, NOW)
  attributes.nested.id = 'prod'
  expect(capability.boundAttributes).toEqual({ nested: { id: 'dev' } })
  expect(() => { (capability.boundAttributes.nested as { id: string }).id = 'prod' }).toThrow()
})
