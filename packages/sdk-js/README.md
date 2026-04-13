# OpenKedge JavaScript SDK (`@openkedge/sdk-js`)

The OpenKedge JavaScript SDK is the fastest and safest way to execute governed mutations for your cloud infrastructure and agents. It provides a familiar, Developer-First experience—similar to standard cloud SDKs—but with policy evaluation, intent tracing, and safety validations built into every call.

## Installation

Install the package via npm or yarn:

```bash
npm install @openkedge/sdk-js
# or
yarn add @openkedge/sdk-js
```

> **Note:** To operate, you need an implementation of the `OpenKedgeEngine`. This can either be instantiated locally via standard engine libraries or injected as an adapter communicating over HTTP.

## Initialize the Client

Initialize the SDK by providing an `OpenKedgeEngine` instance and any defaults, such as the initial actor conducting the actions.

```typescript
import { OpenKedgeClient } from '@openkedge/sdk-js'
import { engine } from './my-engine-setup' // Swap in your actual engine

const okg = new OpenKedgeClient({
  engine,
  defaultActor: 'automation-agent-1',
  debug: true // Recommended for development verbose logging
})
```

---

## 🚀 Usage Examples

### 1. Basic Action Execution

The most straightforward way to execute an intent. If an `actor` is not explicitly provided during the call, the initial SDK `defaultActor` configuration is utilized.

```typescript
const result = await okg.execute('ec2:DescribeInstances', {
  region: 'us-west-2'
})

if (result.success) {
  console.log('Action Approved & Executed!')
}
```

### 2. Handling Governance Blocks (No Throws!)

Because OpenKedge is built on intent-based governance, safety violations are gracefully blocked rather than indiscriminately throwing runtime exceptions. This protects the uptime of your orchestration plane.

```typescript
const result = await okg.execute('ec2:TerminateInstances', {
  instanceIds: ['i-0abcd1234efgh5678']
})

if (!result.success) {
  // Understand exactly why the policy blocked the action
  const summary = await result.summary()
  console.error(`Status: ${summary.outcome}`)
  console.error(`Blocked Reason: ${summary.reason}`)

  // Immediately retrieve the cryptographic trace (IEEC) 
  const replay = await result.replay()
  console.log('Trace Chain:', replay.events)
}
```

### 3. Advanced Fluent API Builder

For more complex actions where contexts and logic trees may construct payload boundaries piecemeal, utilize the chainable `IntentBuilder`.

```typescript
const executeDeployment = async () => {
  const result = await okg
    .intent('ecs:UpdateService')
    .payload({ cluster: 'production', service: 'payment-api' })
    .actor('deploy-pipeline') // Temporarily overrides the defaultActor
    .metadata({ pullRequestId: 'PR-1234', priority: 'high' }) // Inject custom tracing
    .execute()

  if (result.success) {
    console.log(`Successfully completed! Audit ID: ${result.intentId}`)
  }
}
```

### 4. Policy Preview Mode 

Evaluate the viability of a mutation and surface the calculated blast radius *without* actually persisting or invoking side causes.

```typescript
const preview = await okg.preview('s3:DeleteBucket', {
  bucketName: 'critical-data-store'
})

if (!preview.allowed) {
  console.log(`Warning: Action would be blocked.`)
  console.log(`Analysis: ${preview.reasoning}`)
}
```

## Key Capabilities

* **Zero Catastrophic Flow Breaks**: Rejections bounce gracefully into `success: false` constructs allowing orchestration branches to fall back safely.
* **Auto Intent Lineage**: Cryptographic tracing intent `id` generation is obfuscated entirely within execution wrapping.
* **First-Class Typing**: Completely typed TS definitions incorporating Generic typing inferences mapping custom payloads easily `execute<CustomPayload, ExpectedReturn>()`.
