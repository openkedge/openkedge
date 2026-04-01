export type Fact = {
  type: string
  value: any
  attributes?: Record<string, any>
  validity?: {
    start: number
    end?: number
  }
}
