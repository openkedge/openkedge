import { createHash } from 'node:crypto'

export interface GatewayIdentity {
  gatewayId: string
  callerId: string
  delegatedBy: string
}

export interface GatewayLaunchConfig extends GatewayIdentity {
  signingKey: string
}

// Digest only: the fixed secret itself lives in the demo launcher.
const demoKeyDigest = 'e6e4f8bebde745158005b86a93c999149f4e7fb75ae6145a67a77a8aa3874f3f'

function principal(value: string | undefined, name: string): string {
  if (!value || !/^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$/.test(value)) {
    throw new Error(`INVALID_GATEWAY_CONFIG: ${name} must be an explicit nonempty principal identifier`)
  }
  return value
}

export function validateIdentity(value: GatewayIdentity): GatewayIdentity {
  return Object.freeze({
    gatewayId: principal(value.gatewayId, 'gatewayId'),
    callerId: principal(value.callerId, 'callerId'),
    delegatedBy: principal(value.delegatedBy, 'delegatedBy')
  })
}

/** Launcher supplied configuration. No MCP argument can set these claims. */
export function loadGatewayConfig(env: NodeJS.ProcessEnv): GatewayLaunchConfig {
  const identity = validateIdentity({
    gatewayId: env.OKG_GATEWAY_ID ?? '', callerId: env.OKG_CALLER_ID ?? '', delegatedBy: env.OKG_DELEGATED_BY ?? ''
  })
  const encoded = env.OKG_SIGNING_KEY_HEX
  if (!encoded || !/^[a-fA-F0-9]{64}$/.test(encoded)) {
    throw new Error('INVALID_GATEWAY_CONFIG: OKG_SIGNING_KEY_HEX must contain 32 random bytes encoded as 64 hex characters')
  }
  const key = Buffer.from(encoded, 'hex')
  if (new Set(key).size < 17 || key.equals(Buffer.alloc(32))) {
    throw new Error('INVALID_GATEWAY_CONFIG: Signing key is weak')
  }
  if (createHash('sha256').update(key).digest('hex') === demoKeyDigest && env.OKG_DEMO_ONLY !== '1') {
    throw new Error('INVALID_GATEWAY_CONFIG: Demo signing key is permitted only in the demo launcher')
  }
  return Object.freeze({ ...identity, signingKey: encoded.toLowerCase() })
}
