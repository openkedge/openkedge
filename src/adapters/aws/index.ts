import { AwsContextProvider } from './AwsContextProvider'
import { AwsExecutor } from './AwsExecutor'
import { AwsSafetyPolicyEvaluator } from './AwsSafetyPolicyEvaluator'

export function createAwsAdapter() {
  return {
    contextProvider: new AwsContextProvider(),
    executor: new AwsExecutor(),
    policyEvaluator: new AwsSafetyPolicyEvaluator()
  }
}

export { AwsContextProvider } from './AwsContextProvider'
export { AwsExecutor } from './AwsExecutor'
export { AwsSafetyPolicyEvaluator } from './AwsSafetyPolicyEvaluator'
