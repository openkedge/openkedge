# Disposable EC2 termination pilot

This pilot protects one `ec2:TerminateInstances` call for one running test instance tagged `OpenKedgePilot=disposable`. The default MCP server remains a mock and uses no AWS credentials. The pilot requires `OKG_AWS_PILOT_CONFIG`. Even with that file, redemption performs `DescribeInstances` and `TerminateInstances(DryRun=true)` only. A real API request requires both `mutationEnabled: true` in the separate test-account config and `OKG_AWS_PILOT_MUTATE=I_ACCEPT_DISPOSABLE_TEST_INSTANCE_TERMINATION` in the trusted gateway launch environment. The request always sets `SkipOsShutdown=false`.

## AWS authorization and trust boundary

The [EC2 authorization reference](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ec2.html) defines the `instance*` ARN for `TerminateInstances` and supports `aws:ResourceTag/${TagKey}` on this action. The [EC2 API reference](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_TerminateInstances.html) defines `DryRun`: `DryRunOperation` means authorization would succeed, while `UnauthorizedOperation` means it would fail. `DescribeInstances` uses `Resource: "*"` in the IAM policy. [STS session policies](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html) intersect with the execution role's identity policy; they cannot grant permissions beyond it. The role policy and the grant-bound session policy both restrict the exact instance ARN and disposable tag. The session policy also embeds the grant's current time bounds. STS session credentials may physically last 15 minutes; the pilot's grant and in-process identity expire within 30 seconds, and the credentials are never returned to the MCP caller.

The agent role has explicit denies for `ec2:TerminateInstances` and `sts:AssumeRole` on `OpenKedgePilotExecution`. The gateway bootstrap role can describe instances and assume that execution role, and explicitly denies direct termination. The execution role trusts only the gateway role and permits termination of the one configured instance while its `OpenKedgePilot` tag is `disposable`. The gateway checks its exact STS caller ARN and 12-digit test-account ID at startup and during live context resolution. It checks the current policy and grant, exact instance and parameters, running state and tag before STS issuance and again just before DryRun and mutation. The gateway consumes the grant atomically in IEEC before entering the AWS adapter.

AWS authorization is an additional guard, not a substitute for the local evidence and policy checks. A changed tag or state, policy revision, grant, or parameter rejects the call. A successful DryRun returns `status: "validated"`; no mutation occurred. An EC2 API response with a request ID returns `status: "executed"`, meaning AWS accepted the request, not that termination reached a final state. A timeout after the call may have been sent returns `status: "uncertain"`, records `success: false`, and consumes the grant. Reconcile the instance and CloudTrail before considering another request.

There is a narrow race between the last `DescribeInstances` and the EC2 mutation. IAM reevaluates the instance tag on the protected call; AWS does not provide an atomic compare-and-terminate condition for the observed instance state. A state change in that gap remains possible.

CloudTrail identifies the assumed-role session as `openkedge-<proposalId>`; [AWS documents role session names in CloudTrail identity records](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-event-reference-user-identity.html). The IEEC `ExecutionCompleted` result contains `proposalId`, `contractId`, assumed-role ARN, `ec2RequestId` for a successful API response, and the outcome. Match CloudTrail's `TerminateInstances` event by assumed-role session name and request ID, then use the proposal ID to find the grant and IEEC outcome. A DryRun may also carry a `dryRunRequestId`. In an uncertain result, the request ID may be absent; investigate CloudTrail and current EC2 state using the proposal's role session name and target.

## Disposable test-account deployment

Use a separate AWS test account. The commands below require the AWS CLI and an administrator profile **in that test account** for setup. The instance must already be disposable, running, and tagged. If creating it, choose a test subnet, security group, AMI, and instance profile with no production access, then record the returned instance ID:

```sh
export AWS_PILOT_ADMIN_PROFILE=okg-test-admin
export AWS_PILOT_ACCOUNT_ID=123456789012
export AWS_PILOT_REGION=us-east-1
export AWS_PILOT_INSTANCE_ID=i-0123456789abcdef0
export AWS_PILOT_BOOTSTRAP_ARN=arn:aws:iam::123456789012:role/TestAccountPilotBootstrap
aws --profile "$AWS_PILOT_ADMIN_PROFILE" --region "$AWS_PILOT_REGION" ec2 create-tags --resources "$AWS_PILOT_INSTANCE_ID" --tags Key=OpenKedgePilot,Value=disposable
bash examples/aws-pilot/setup.sh
```

`setup.sh` checks the administrator account, exact instance state and tag before creating `OpenKedgePilotGateway`, `OpenKedgePilotAgent`, and `OpenKedgePilotExecution` with their inline policies. It does not create an instance or terminate one. The bootstrap principal needs IAM role-management permission for setup and `sts:AssumeRole` for the two launcher roles. Use separate AWS profiles and OS credential stores for the agent and gateway; the following profile setup is convenient for a disposable single-user test, but its shared bootstrap profile does **not** establish OS credential isolation:

```sh
aws configure set profile.okg-gateway.role_arn "arn:aws:iam::$AWS_PILOT_ACCOUNT_ID:role/OpenKedgePilotGateway"
aws configure set profile.okg-gateway.source_profile "$AWS_PILOT_ADMIN_PROFILE"
aws configure set profile.okg-gateway.role_session_name openkedge-gateway
aws configure set profile.okg-agent.role_arn "arn:aws:iam::$AWS_PILOT_ACCOUNT_ID:role/OpenKedgePilotAgent"
aws configure set profile.okg-agent.source_profile "$AWS_PILOT_ADMIN_PROFILE"
aws configure set profile.okg-agent.role_session_name openkedge-agent
mkdir -p .openkedge-pilot
chmod 700 .openkedge-pilot
```

Create the test-account config and a dedicated policy. The STS ARNs are exact session identities checked by the gateway and preflight. Set `mutationEnabled` to `false` for the first run:

```sh
export AWS_PILOT_GATEWAY_ARN="$(AWS_PROFILE=okg-gateway aws sts get-caller-identity --query Arn --output text)"
export AWS_PILOT_AGENT_ARN="$(AWS_PROFILE=okg-agent aws sts get-caller-identity --query Arn --output text)"
node - <<'NODE'
const fs = require('node:fs');
const e = process.env;
const config = { testAccountId: e.AWS_PILOT_ACCOUNT_ID, region: e.AWS_PILOT_REGION,
  instanceId: e.AWS_PILOT_INSTANCE_ID, gatewayPrincipalArn: e.AWS_PILOT_GATEWAY_ARN,
  agentPrincipalArn: e.AWS_PILOT_AGENT_ARN,
  executionRoleArn: `arn:aws:iam::${e.AWS_PILOT_ACCOUNT_ID}:role/OpenKedgePilotExecution`,
  requiredTag: { key: 'OpenKedgePilot', value: 'disposable' }, mutationEnabled: false };
const policy = { protocolVersion: 1, version: 'aws-pilot-v1',
  allowedInstanceIds: [e.AWS_PILOT_INSTANCE_ID], protectedInstanceIds: [],
  allowSkipOsShutdown: false, instances: {}, rules: [] };
fs.writeFileSync('.openkedge-pilot/config.json', JSON.stringify(config, null, 2), { mode: 0o600 });
fs.writeFileSync('.openkedge-pilot/policy.json', JSON.stringify(policy, null, 2), { mode: 0o600 });
NODE
```

Run the bypass preflight with **agent** credentials, then launch the independent MCP SDK client with **gateway** credentials. The preflight must see explicit authorization denials for both direct termination DryRun and execution-role assumption. An API or credential failure is inconclusive and fails the command. The MCP example discovers tools, admits the target, performs a read-only context lookup and authorized DryRun, denies a `skipOsShutdown` proposal, and inspects IEEC evidence:

```sh
export OKG_AWS_PILOT_CONFIG="$PWD/.openkedge-pilot/config.json"
AWS_PROFILE=okg-agent npm run pilot:preflight
export OKG_POLICY_FILE="$PWD/.openkedge-pilot/policy.json"
export OKG_EVIDENCE_DB="$PWD/.openkedge-pilot/evidence.sqlite"
export OKG_GATEWAY_ID=aws-pilot-1 OKG_CALLER_ID=pilot-agent OKG_DELEGATED_BY=pilot-operator
export OKG_SIGNING_KEY_HEX="$(openssl rand -hex 32)"
OKG_AWS_GATEWAY_PROFILE=okg-gateway AWS_PROFILE=okg-agent npm run pilot:mcp
```

Only after reviewing the DryRun result, in the disposable account, opt in to the real API call with a fresh instance and evidence DB. The exact command below changes the config and adds the separate launch flag. `npm run pilot:mcp` will terminate that instance if the live checks pass:

```sh
node - <<'NODE'
const fs = require('node:fs'); const p = '.openkedge-pilot/config.json';
const c = JSON.parse(fs.readFileSync(p, 'utf8')); c.mutationEnabled = true;
fs.writeFileSync(p, JSON.stringify(c, null, 2), { mode: 0o600 });
NODE
export OKG_EVIDENCE_DB="$PWD/.openkedge-pilot/mutation-evidence.sqlite"
OKG_AWS_PILOT_MUTATE=I_ACCEPT_DISPOSABLE_TEST_INSTANCE_TERMINATION OKG_AWS_GATEWAY_PROFILE=okg-gateway AWS_PROFILE=okg-agent npm run pilot:mcp
```

Inspect the event in CloudTrail for `TerminateInstances` and compare the returned `ec2RequestId` and role session name to IEEC. Teardown removes the roles. If the test instance is still present, terminate it separately with the administrator profile after confirming its ID:

```sh
AWS_PROFILE="$AWS_PILOT_ADMIN_PROFILE" aws --region "$AWS_PILOT_REGION" ec2 terminate-instances --instance-ids "$AWS_PILOT_INSTANCE_ID"
bash examples/aws-pilot/teardown.sh
```

## Proof limits and remaining bypasses

The standard suite and mock demo use no AWS account; they prove local grant, policy, live-guard simulation, one-use evidence, and failure classification. `pilot:preflight` and `pilot:mcp` against a test account provide separate AWS verification when explicitly run. This repository does not claim that a locally supplied `OKG_CALLER_ID` authenticates a remote human or MCP client. Stdio is one trusted-launcher-attested caller per process. The example client and gateway share a process launch environment for functional testing; a real deployment must isolate gateway credentials and signing key from the agent OS user and restrict who can launch or alter the gateway. Administrator or bootstrap credentials, extra attached IAM policies, direct AWS API or network access under another principal, another executor, writable policy/evidence files, and unprotected launcher pipes remain bypass paths. The preflight tests only the configured agent principal at that moment; it cannot prove every principal or future IAM change is safe.
