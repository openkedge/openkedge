import type {
  BlastRadius,
  BlastRadiusPolicyConfig,
  BlastRadiusPolicyDecision
} from './BlastRadiusTypes'

export class BlastRadiusPolicy {
  constructor(
    private readonly config: BlastRadiusPolicyConfig = {
      allowHighRisk: true
    }
  ) {}

  evaluate(blastRadius: BlastRadius): BlastRadiusPolicyDecision {
    const reasons = [...blastRadius.reasons]

    if (
      this.config.maxAllowed !== undefined &&
      blastRadius.resourceCount > this.config.maxAllowed
    ) {
      reasons.push(
        `Blocked because resource count ${blastRadius.resourceCount} exceeds the configured maximum of ${this.config.maxAllowed}`
      )

      return {
        allowed: false,
        reasons
      }
    }

    switch (blastRadius.riskLevel) {
      case 'CRITICAL':
        reasons.push('Blocked due to CRITICAL blast radius')
        return {
          allowed: false,
          reasons
        }
      case 'HIGH':
        reasons.push(
          this.config.allowHighRisk === false
            ? 'Blocked because HIGH blast radius is disabled by policy'
            : 'High-risk mutation allowed with warning'
        )
        return {
          allowed: this.config.allowHighRisk !== false,
          reasons
        }
      case 'MEDIUM':
        reasons.push('Medium blast radius accepted by policy')
        return {
          allowed: true,
          reasons
        }
      case 'LOW':
      default:
        reasons.push('Low blast radius accepted by policy')
        return {
          allowed: true,
          reasons
        }
    }
  }
}
