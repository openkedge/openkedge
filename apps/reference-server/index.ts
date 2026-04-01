import express from 'express'
import { MemoryEventStore } from '../packages/store'
import { proposalsRouter } from './routes/proposals'
import { stateRouter } from './routes/state'

const app = express()
app.use(express.json())

const store = new MemoryEventStore()

app.use('/proposals', proposalsRouter(store))
app.use('/state', stateRouter(store))

app.listen(3000, () => {
  console.log('Reference server running on port 3000')
})
