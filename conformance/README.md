# Controller protocol conformance kit v1

The kit contains five [JSON Schemas](../schemas/control-policy-bundle.v1.schema.json) for bundles, acknowledgements, permits, outcomes and status; signed valid bundles, valid proposal and permit examples, and invalid unsigned, tampered, malformed, rollback, proposal and permit fixtures under [`fixtures`](./fixtures); and [`run.mjs`](./run.mjs). The fixture public key is test-only. Never trust it in a deployed gateway.

From the repository root, run fixture validation without a controller or AWS account:

```sh
npm run conformance:controller
```

For a separate MCP gateway implementation, start a disposable controller with an initially allowed mock target, then set the following and run the same command. The command must accept a JSON array of arguments. The runner launches that gateway through the official MCP SDK stdio transport, discovers the RFC-0006 tools, admits the configured mock target, tests target substitution, executes the permitted mock action and matches the controller outcome to IEEC, activates a new controller epoch that removes the target, checks acknowledgement, old-grant rejection and denial, and verifies replay integrity. It restores the original policy in a later epoch. Use a dedicated controller and gateway because this changes controller policy.

```sh
export OKG_CONFORMANCE_GATEWAY_COMMAND=/absolute/path/to/gateway
export OKG_CONFORMANCE_GATEWAY_ARGS='["--stdio"]'
export OKG_CONFORMANCE_GATEWAY_ID=your-gateway-id
export OKG_CONFORMANCE_CONTROLLER_URL=https://controller.example
export OKG_CONFORMANCE_ADMIN_TOKEN=your-disposable-controller-admin-token
export OKG_CONFORMANCE_POLICY_FILE=/absolute/path/to/initial-policy.json
npm run conformance:controller
```

The gateway process also needs its own launch configuration: its caller/delegator identity, grant signing key, controller URL, per-gateway token, pinned Ed25519 public key, private local cache path and IEEC database. The runner removes the administrator token from the gateway child's environment. See [RFC-0007](../rfcs/0007-controller-gateway-policy.md) for the endpoint fields, auth and outage behavior.

The runner checks one gateway's MCP behavior and controller epoch transition. It does not certify token storage, TLS termination, crash durability, concurrent redemption, the final-check/API gap, AWS IAM or CloudTrail. The repository's [`demo:controller`](../examples/controller-multiprocess.mjs) exercises two independently stored gateways and a second MCP client process over loopback HTTP. An external implementation can reproduce those tests using the schemas and the documented request/response contract, but the kit does not provide a language-neutral SDK.
