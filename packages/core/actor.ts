export type Actor = {
  id: string
  type: 'owner' | 'verified_agent' | 'system' | 'unverified_agent'
  trust: number
}
