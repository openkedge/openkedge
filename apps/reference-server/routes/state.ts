import { Router } from 'express'
import { EventStore } from '../../packages/store'
import { reduceEvents } from '../../packages/reducer'

export function stateRouter(store: EventStore) {
  const router = Router()
  
  router.get('/:entityId', (req, res) => {
    const entityId = req.params.entityId
    const events = store.getEvents(entityId)
    const state = reduceEvents(entityId, events)
    res.json(state)
  })

  return router
}
