import type { ReplayResult } from '../types'

export function CapabilityLineage({ replay, onNavigate }: { replay: ReplayResult; onNavigate: (id: string) => void }) {
  if (!replay.capabilityLinks?.length) return null
  return (
    <section aria-label="Capability lineage" className="mt-6 rounded-2xl border border-violet-300/20 bg-violet-300/5 p-4">
      <h3 className="text-xs uppercase tracking-[0.2em] text-violet-200">Causal capability chain</h3>
      {replay.capabilityLinks.map(link => (
        <div key={link.tokenId} className="mt-4">
          <button type="button" onClick={() => onNavigate(link.sourceProposalId)} className="max-w-full rounded-xl border border-violet-300/30 bg-violet-400/10 p-3 text-left text-sm text-violet-100 hover:bg-violet-400/20">
            <span className="font-semibold">{link.sourceVerified ? 'READ / PROBE' : 'Unverified source'} · {link.sourceAction ?? 'Source proposal'}</span>
            <span className="mt-1 block break-all text-xs text-slate-400">{link.sourceProposalId}</span>
          </button>
          <div className="ml-5 border-l-2 border-dotted border-violet-300/60 py-4 pl-5">
            <span className={`inline-block rounded-full px-3 py-1 text-xs ${link.capabilityVerified ? 'bg-emerald-400/15 text-emerald-200' : 'bg-rose-400/15 text-rose-200'}`}>
              {link.capabilityVerified ? 'Capability verified' : 'Capability not accepted'}
            </span>
            <p className="mt-2 break-all font-mono text-xs text-slate-400">Token {link.tokenId}</p>
            <p className="mt-1 break-all text-xs text-slate-300">Bound: {JSON.stringify(link.boundAttributes)}</p>
          </div>
          <div className="ml-5 rounded-xl border border-white/10 bg-white/5 p-3 text-sm text-slate-200">
            <strong>MUTATION · {replay.originalIntent.type}</strong>
            <p className="mt-1 break-all text-xs text-slate-400">{replay.intentId}</p>
            <p className="mt-1 break-all text-xs">Requested: {JSON.stringify(replay.originalIntent.payload)}</p>
          </div>
        </div>
      ))}
    </section>
  )
}
