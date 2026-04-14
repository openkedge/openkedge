import { EventHasher } from '../event/EventHasher'

import { EvidenceLink, GENESIS_PREV_LINK_HASH } from './EvidenceLink'
import type { EvidenceLinkInput, EvidenceLinkRecord } from './EvidenceTypes'

export interface EvidenceChainRecord {
  readonly version: string
  readonly traceId: string | null
  readonly links: readonly EvidenceLinkRecord[]
}

export class EvidenceChain {
  readonly version: string
  readonly traceId: string | null
  readonly links: readonly EvidenceLink[]

  constructor(
    links: readonly EvidenceLink[] = [],
    options: {
      version?: string
      traceId?: string | null
    } = {}
  ) {
    this.version = options.version ?? 'rfc-0005'
    this.traceId = options.traceId ?? links[0]?.identity.traceId ?? null
    this.links = Object.freeze([...links])

    this.assertTraceConsistency()
    this.assertIntegrity()
    Object.freeze(this)
  }

  append(linkOrInput: EvidenceLink | EvidenceLinkInput): EvidenceChain {
    const link =
      linkOrInput instanceof EvidenceLink
        ? linkOrInput
        : EvidenceLink.create(linkOrInput)
    const previousHash =
      this.links[this.links.length - 1]?.proof.link_hash ?? GENESIS_PREV_LINK_HASH
    const finalized = link.with_prev_link_hash(previousHash)
    const nextTraceId = this.traceId ?? finalized.identity.traceId

    if (this.traceId && finalized.identity.traceId !== this.traceId) {
      throw new Error('EvidenceChain only supports a single traceId')
    }

    return new EvidenceChain([...this.links, finalized], {
      version: this.version,
      traceId: nextTraceId
    })
  }

  appendLink(linkOrInput: EvidenceLink | EvidenceLinkInput): EvidenceChain {
    return this.append(linkOrInput)
  }

  verify_integrity(): boolean {
    try {
      this.assertIntegrity()
      return true
    } catch {
      return false
    }
  }

  verifyIntegrity(): boolean {
    return this.verify_integrity()
  }

  toJSON(): EvidenceChainRecord {
    return {
      version: this.version,
      traceId: this.traceId,
      links: this.links.map((link) => link.toJSON())
    }
  }

  export_jsonld(baseId?: string): string {
    return JSON.stringify(this.toJSONLD(baseId), null, 2)
  }

  exportJsonLd(baseId?: string): string {
    return this.export_jsonld(baseId)
  }

  toJSONLD(baseId?: string) {
    const rootId =
      baseId ??
      `urn:openkedge:evidence-chain:${
        this.traceId ?? EventHasher.hashValue(this.toJSON()).slice(0, 16)
      }`

    return {
      '@context': {
        '@vocab': 'https://openkedge.dev/ns/evidence#',
        okg: 'https://openkedge.dev/ns/evidence#'
      },
      '@id': rootId,
      '@type': 'okg:EvidenceChain',
      version: this.version,
      traceId: this.traceId,
      linkCount: this.links.length,
      hasPart: this.links.map((link, index) =>
        link.toJSONLD(`${rootId}#link-${index + 1}`, index + 1)
      )
    }
  }

  private assertTraceConsistency(): void {
    if (this.links.length === 0) {
      return
    }

    const expectedTraceId = this.traceId ?? this.links[0].identity.traceId

    for (const link of this.links) {
      if (link.identity.traceId !== expectedTraceId) {
        throw new Error('EvidenceChain contains multiple traceIds')
      }
    }
  }

  private assertIntegrity(): void {
    let previousHash = GENESIS_PREV_LINK_HASH

    for (const link of this.links) {
      if (!link.verify_integrity()) {
        throw new Error('EvidenceChain contains a tampered link')
      }

      if (link.proof.prev_link_hash !== previousHash) {
        throw new Error('EvidenceChain contains a broken prev_link_hash')
      }

      previousHash = link.proof.link_hash
    }
  }
}
