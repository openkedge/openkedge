import { randomUUID } from 'crypto'

async function runScenario() {
  const isBlastRadiusScenario = process.argv.includes('--blast-radius')
  const isCriticalScenario = process.argv.includes('--critical')
  
  // Base scenario is allowed: terminate 2 non-critical instances
  let targetIds = ['i-0000006', 'i-0000007']
  
  if (isBlastRadiusScenario) {
    // Target 25 instances (> 20 threshold)
    targetIds = Array.from({ length: 25 }).map((_, i) => `i-${String(i + 6).padStart(7, '0')}`)
  } else if (isCriticalScenario) {
    // Target a critical instance (i-0000001 is critical)
    targetIds = ['i-0000001', 'i-0000006']
  } else {
    // By default, let's just do blast radius as requested by the demo story
    targetIds = Array.from({ length: 25 }).map((_, i) => `i-${String(i + 6).padStart(7, '0')}`)
  }

  const intent = {
    id: randomUUID(),
    type: 'ec2:TerminateInstances',
    payload: targetIds,
    metadata: {
      actor: 'agent-demo',
      timestamp: Date.now()
    }
  }

  console.log(`\n===========================================`)
  console.log(`🚀 SUBMITTING INTENT TO OPENKEDGE ENGINE`)
  console.log(`===========================================`)
  console.log(`Type:          ${intent.type}`)
  console.log(`Intent ID:     ${intent.id}`)
  console.log(`Targets Count: ${targetIds.length}`)
  console.log(`Targets:       ${JSON.stringify(targetIds)}`)
  console.log(`\nSending request to localhost:3001/intent...\n`)

  try {
    const response = await fetch('http://localhost:3001/intent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(intent)
    })

    const result = await response.json() as any
    
    console.log(`===========================================`)
    console.log(`🛡️  EXECUTION RESULT`)
    console.log(`===========================================`)
    console.log(`Success: ${result.success}`)
    if (result.error) {
      console.log(`Error:   ${result.error}`)
    }
    console.log(`\nView detailed replay tracking at: http://localhost:5173/?intentId=${intent.id}\n`)
    
  } catch (err: any) {
    console.error(`Failed to connect to demo server. Ensure apps/demo-server is running on port 3001.`, err.message)
  }
}

runScenario()
