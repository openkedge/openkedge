import { AwsContextProvider } from './AwsContextProvider'
import { AwsExecutor } from './AwsExecutor'
import { AwsIdentityProvider, type AwsIdentityProviderOptions } from './AwsIdentityProvider'
import { AwsSafetyPolicyEvaluator } from './AwsSafetyPolicyEvaluator'

export interface CreateAwsAdapterOptions extends AwsIdentityProviderOptions {
  region?: string
}

export function createAwsAdapter(options: CreateAwsAdapterOptions = {}) {
  return {
    contextProvider: new AwsContextProvider(),
    executor: new AwsExecutor({
      region: options.region
    }),
    policyEvaluator: new AwsSafetyPolicyEvaluator(),
    identityProvider: new AwsIdentityProvider(options)
  }
}

export { AwsContextProvider } from './AwsContextProvider'
export { AwsExecutor } from './AwsExecutor'
export { AwsIdentityProvider, generatePolicy } from './AwsIdentityProvider'
export { AwsSafetyPolicyEvaluator } from './AwsSafetyPolicyEvaluator'
