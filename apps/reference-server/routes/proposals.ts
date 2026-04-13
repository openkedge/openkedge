import { Router } from 'express'
import { submitProposal } from '../../packages/engine'
import { EventStore } from '../../packages/store'

export function proposalsRouter(store: EventStore) {
  const router = Router()
  
  router.post('/', async (req, res) => {
    const decision = await submitProposal(req.body, store)
    res.json(decision)
  })

  return router
}
