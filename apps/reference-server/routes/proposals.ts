import { Router } from 'express'
import { submitProposal } from '../../packages/engine'
import { EventStore } from '../../packages/store'

export function proposalsRouter(store: EventStore) {
  const router = Router()
  
  router.post('/', (req, res) => {
    const decision = submitProposal(req.body, store)
    res.json(decision)
  })

  return router
}
