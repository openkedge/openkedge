# Controller interface: implementer quickstart

The reference implementation is [`src/control-plane/controller.ts`](../src/control-plane/controller.ts) and [`src/control-plane/client.ts`](../src/control-plane/client.ts). The wire contract is [RFC-0007](../rfcs/0007-controller-gateway-policy.md) and the [conformance kit](../conformance/README.md). All HTTP bodies are JSON with `protocolVersion: 1`; the existing MCP proposal, decision, contract and redemption payloads remain those of RFC-0006. Do not let a caller choose its action class or supply policy identity as a tool argument.

Bundle signing uses Ed25519 with an SPKI PEM public key and a base64 signature. Build the signed bytes by recursively sorting object keys lexicographically, retaining array order, and JSON-encoding primitives exactly as `JSON.stringify` does; reject undefined, functions, non-finite numbers and non-JSON objects. Sign every bundle field except `signature`. `issuedAt`, `leaseUntil` and `activatedAt` are Unix milliseconds. The reference verifier rejects leases longer than 60 seconds. The revision is `epoch:version@sha256(canonical policy JSON)`. A gateway authenticates each request with `Authorization: Bearer <gateway token>` and `x-okg-gateway-id: <gateway ID>`; the controller maps each ID to its own token. `401` means unauthorized; `409` means an acknowledgement, permit or outcome conflict; malformed requests return `400`.

To run the reference controller locally from the repository root, use two terminals and a disposable directory. The controller reads the bootstrap policy once. Later distribution uses authenticated HTTP and per-gateway local caches, with no shared policy file:

```sh
npm run build
mkdir -p .openkedge-controller
chmod 700 .openkedge-controller
openssl genpkey -algorithm Ed25519 -out .openkedge-controller/private.pem
openssl pkey -in .openkedge-controller/private.pem -pubout -out .openkedge-controller/public.pem
cp policies/gateway-local.json .openkedge-controller/bootstrap-policy.json
node - <<'NODE'
const fs = require('node:fs'); const crypto = require('node:crypto');
fs.writeFileSync('.openkedge-controller/tokens.json', JSON.stringify({ gatewayOne: crypto.randomBytes(32).toString('hex') }), { mode: 0o600 });
fs.writeFileSync('.openkedge-controller/admin-token', crypto.randomBytes(32).toString('hex'), { mode: 0o600 });
NODE
export OKG_CONTROLLER_DB="$PWD/.openkedge-controller/controller.sqlite"
export OKG_CONTROLLER_PRIVATE_KEY="$PWD/.openkedge-controller/private.pem"
export OKG_CONTROLLER_INITIAL_POLICY="$PWD/.openkedge-controller/bootstrap-policy.json"
export OKG_CONTROLLER_TOKENS_FILE="$PWD/.openkedge-controller/tokens.json"
export OKG_CONTROLLER_ADMIN_TOKEN="$(cat .openkedge-controller/admin-token)"
export OKG_CONTROLLER_PORT=8765
node dist/control-plane/controller.js
```

In a second terminal, launch an MCP gateway through the conformance runner. Set the gateway's distinct identity, evidence DB and cache path, its token, the pinned public key, and the controller URL. These commands exercise a **mock** action and update the disposable controller policy; they do not use AWS:

```sh
export OKG_GATEWAY_ID=gatewayOne OKG_CALLER_ID=test-agent OKG_DELEGATED_BY=test-operator
export OKG_SIGNING_KEY_HEX="$(openssl rand -hex 32)"
export OKG_EVIDENCE_DB="$PWD/.openkedge-controller/gateway-one-evidence.sqlite"
export OKG_CONTROLLER_STATE_FILE="$PWD/.openkedge-controller/gateway-one-cache.json"
export OKG_CONTROLLER_PUBLIC_KEY="$PWD/.openkedge-controller/public.pem"
export OKG_CONTROLLER_GATEWAY_TOKEN="$(node -p "require('./.openkedge-controller/tokens.json').gatewayOne")"
export OKG_CONTROLLER_URL=http://127.0.0.1:8765
export OKG_CONFORMANCE_GATEWAY_COMMAND=node
export OKG_CONFORMANCE_GATEWAY_ARGS="[\"$PWD/dist/gateway/mcp-server.js\"]"
export OKG_CONFORMANCE_GATEWAY_ID=gatewayOne
export OKG_CONFORMANCE_CONTROLLER_URL="$OKG_CONTROLLER_URL"
export OKG_CONFORMANCE_ADMIN_TOKEN="$(cat .openkedge-controller/admin-token)"
export OKG_CONFORMANCE_POLICY_FILE="$PWD/.openkedge-controller/bootstrap-policy.json"
npm run conformance:controller
```

For another implementation, pin the Ed25519 public key, parse and validate the entire bundle against `control-policy-bundle.v1.schema.json` and the policy schema, verify the signature over canonical JSON excluding `signature`, reject an epoch decrease or a different bundle at the same epoch, durably persist the highest verified bundle, and only then send `POST /v1/ack`. Authenticate every controller request with that gateway's token. A consequential action must fetch current policy at admission and redemption and acquire a permit at dispatch. A bounded action can use only a still-valid signed lease. `POST /v1/outcome` should be sent after local evidence is durable; implement a retry queue if remote evidence completeness is required across outages.

The loopback reference server speaks HTTP only and refuses non-loopback binding. A remote installation needs a TLS reverse proxy, protected bearer tokens, separate OS identities and storage, and a downstream API fence for stronger revocation. The conformance runner checks the wire behavior of one gateway; the [measured two-gateway scenario](./controller-measurements.md) adds activation acknowledgements, outage/reconnect and the final-check/API interval. Neither proves remote MCP caller identity or instantaneous AWS revocation.
