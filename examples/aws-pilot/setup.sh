#!/usr/bin/env bash
set -euo pipefail
: "${AWS_PILOT_ADMIN_PROFILE:?Set AWS_PILOT_ADMIN_PROFILE}"
: "${AWS_PILOT_ACCOUNT_ID:?Set AWS_PILOT_ACCOUNT_ID}"
: "${AWS_PILOT_REGION:?Set AWS_PILOT_REGION}"
: "${AWS_PILOT_INSTANCE_ID:?Set AWS_PILOT_INSTANCE_ID}"
: "${AWS_PILOT_BOOTSTRAP_ARN:?Set AWS_PILOT_BOOTSTRAP_ARN to the exact IAM principal allowed to assume the two launcher roles}"
[[ "$AWS_PILOT_ACCOUNT_ID" =~ ^[0-9]{12}$ ]] || exit 2
[[ "$AWS_PILOT_INSTANCE_ID" =~ ^i-[a-f0-9]{17}$ ]] || exit 2
[[ "$AWS_PILOT_BOOTSTRAP_ARN" == arn:aws:iam::"$AWS_PILOT_ACCOUNT_ID":* ]] || exit 2
account="$(aws --profile "$AWS_PILOT_ADMIN_PROFILE" sts get-caller-identity --query Account --output text)"
[[ "$account" == "$AWS_PILOT_ACCOUNT_ID" ]] || { echo 'Test account mismatch' >&2; exit 2; }
state="$(aws --profile "$AWS_PILOT_ADMIN_PROFILE" --region "$AWS_PILOT_REGION" ec2 describe-instances --instance-ids "$AWS_PILOT_INSTANCE_ID" --query 'Reservations[0].Instances[0].State.Name' --output text)"
tag="$(aws --profile "$AWS_PILOT_ADMIN_PROFILE" --region "$AWS_PILOT_REGION" ec2 describe-tags --filters "Name=resource-id,Values=$AWS_PILOT_INSTANCE_ID" 'Name=key,Values=OpenKedgePilot' --query 'Tags[0].Value' --output text)"
[[ "$state" == running && "$tag" == disposable ]] || { echo 'Instance must be running and tagged OpenKedgePilot=disposable' >&2; exit 2; }
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cat > "$work/launcher-trust.json" <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"AWS":"$AWS_PILOT_BOOTSTRAP_ARN"},"Action":"sts:AssumeRole"}]}
EOF
gateway_role="arn:aws:iam::$AWS_PILOT_ACCOUNT_ID:role/OpenKedgePilotGateway"
execution_role="arn:aws:iam::$AWS_PILOT_ACCOUNT_ID:role/OpenKedgePilotExecution"
cat > "$work/execution-trust.json" <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"AWS":"$gateway_role"},"Action":"sts:AssumeRole"}]}
EOF
cat > "$work/gateway-policy.json" <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"ec2:DescribeInstances","Resource":"*","Condition":{"StringEquals":{"aws:RequestedRegion":"$AWS_PILOT_REGION"}}},{"Effect":"Allow","Action":"sts:AssumeRole","Resource":"$execution_role"},{"Effect":"Deny","Action":"ec2:TerminateInstances","Resource":"*"}]}
EOF
cat > "$work/agent-policy.json" <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"ec2:DescribeInstances","Resource":"*","Condition":{"StringEquals":{"aws:RequestedRegion":"$AWS_PILOT_REGION"}}},{"Effect":"Deny","Action":"ec2:TerminateInstances","Resource":"*"},{"Effect":"Deny","Action":"sts:AssumeRole","Resource":"$execution_role"}]}
EOF
cat > "$work/execution-policy.json" <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"ec2:TerminateInstances","Resource":"arn:aws:ec2:$AWS_PILOT_REGION:$AWS_PILOT_ACCOUNT_ID:instance/$AWS_PILOT_INSTANCE_ID","Condition":{"StringEquals":{"aws:ResourceTag/OpenKedgePilot":"disposable","aws:RequestedRegion":"$AWS_PILOT_REGION"}}}]}
EOF
aws --profile "$AWS_PILOT_ADMIN_PROFILE" iam create-role --role-name OpenKedgePilotGateway --assume-role-policy-document "file://$work/launcher-trust.json" >/dev/null
aws --profile "$AWS_PILOT_ADMIN_PROFILE" iam create-role --role-name OpenKedgePilotAgent --assume-role-policy-document "file://$work/launcher-trust.json" >/dev/null
aws --profile "$AWS_PILOT_ADMIN_PROFILE" iam create-role --role-name OpenKedgePilotExecution --assume-role-policy-document "file://$work/execution-trust.json" >/dev/null
aws --profile "$AWS_PILOT_ADMIN_PROFILE" iam put-role-policy --role-name OpenKedgePilotGateway --policy-name OpenKedgePilotGatewayAccess --policy-document "file://$work/gateway-policy.json"
aws --profile "$AWS_PILOT_ADMIN_PROFILE" iam put-role-policy --role-name OpenKedgePilotAgent --policy-name OpenKedgePilotAgentAccess --policy-document "file://$work/agent-policy.json"
aws --profile "$AWS_PILOT_ADMIN_PROFILE" iam put-role-policy --role-name OpenKedgePilotExecution --policy-name OpenKedgePilotTerminate --policy-document "file://$work/execution-policy.json"
echo 'Created pilot roles. Configure separate agent and gateway AWS profiles before preflight.'
