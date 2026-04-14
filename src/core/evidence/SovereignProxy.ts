export class SovereignProxy {
  private readonly maskedToReal = new Map<string, string>()
  private readonly realToMasked = new Map<string, string>()
  private counter = 0

  constructor(private readonly prefix: string = 'resource') {}

  mask_resource(realResource: string): string {
    const existing = this.realToMasked.get(realResource)

    if (existing) {
      return existing
    }

    this.counter += 1

    const masked = `${this.prefix}-${String(this.counter).padStart(4, '0')}`
    this.realToMasked.set(realResource, masked)
    this.maskedToReal.set(masked, realResource)

    return masked
  }

  maskResource(realResource: string): string {
    return this.mask_resource(realResource)
  }

  unmask_resource(maskedResource: string): string {
    const realResource = this.maskedToReal.get(maskedResource)

    if (!realResource) {
      throw new Error(`Unknown masked resource: ${maskedResource}`)
    }

    return realResource
  }

  unmaskResource(maskedResource: string): string {
    return this.unmask_resource(maskedResource)
  }
}
