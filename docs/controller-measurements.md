# Local controller run: observed measurements

Command: `npm run demo:controller` on 2026-09-24. Conditions: one local controller process restarted once, two independent gateway processes with separate SQLite evidence and signed-bundle cache files, two independent MCP SDK client processes, loopback HTTP controller transport, mock `ec2:TerminateInstances`, 300 ms injected delay between the dispatch permit and mock API call, no AWS account or network fault injection beyond stopping the controller. The live conformance runner also passed against a later fresh gateway process, after the measured phase. Timings are wall-clock observations from one run on this machine, not a service-level objective.

| Metric | Observed milliseconds or count |
| --- | --- |
| Controller activation, five updates | 1.22, 1.02, 299.59, 3.35, 0.98 ms |
| Both gateway acknowledgements after update request | 22.98, 24.53, 330.85, 25.18, 24.43 ms |
| Eight admission calls | 11.45, 5.45, 3.99, 4.45, 3.87, 4.05, 2.75, 1.98 ms |
| Six redemption calls | 3.71, 8.50, 312.72, 3.37, 4.75, 1.47 ms |
| False rejection during the pending update | 1 of 1 attempted redemption of an otherwise admitted old-epoch grant; the controller returned `POLICY_UPDATE_PENDING` to prevent another old-policy dispatch. The admission in the same interval was allowed. |
| Evidence completeness | 2 of 2 permitted API outcomes matched a completed IEEC event hash; a denied proposal's replay integrity also verified. |

The third update waited about 300 ms for an already-issued permit while the mock adapter paused immediately before its underlying call. Its action completed under the old epoch, then the update activated and both gateways acknowledged epoch 4. A new permit requested during the wait was rejected. After controller stop, a consequential admission returned `CONTROLLER_UNAVAILABLE`; reconnect retained the durable epoch and later execution succeeded after another update. The fifth update advanced the epoch without changing policy content and invalidated an older unredeemed grant. One sample does not establish latency percentiles, network partition behavior, or AWS revocation timing.
