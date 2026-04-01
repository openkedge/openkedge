import { Fact } from './fact'

export type DerivedState = {
  entityId: string
  facts: Fact[]
  lastUpdated: number
}
