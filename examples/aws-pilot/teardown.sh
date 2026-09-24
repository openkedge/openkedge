#!/usr/bin/env bash
set -euo pipefail
: "${AWS_PILOT_ADMIN_PROFILE:?Set AWS_PILOT_ADMIN_PROFILE}"
: "${AWS_PILOT_ACCOUNT_ID:?Set AWS_PILOT_ACCOUNT_ID}"
account="$(aws --profile "$AWS_PILOT_ADMIN_PROFILE" sts get-caller-identity --query Account --output text)"
[[ "$account" == "$AWS_PILOT_ACCOUNT_ID" ]] || { echo 'Test account mismatch' >&2; exit 2; }
aws --profile "$AWS_PILOT_ADMIN_PROFILE" iam delete-role-policy --role-name OpenKedgePilotExecution --policy-name OpenKedgePilotTerminate
aws --profile "$AWS_PILOT_ADMIN_PROFILE" iam delete-role-policy --role-name OpenKedgePilotGateway --policy-name OpenKedgePilotGatewayAccess
aws --profile "$AWS_PILOT_ADMIN_PROFILE" iam delete-role-policy --role-name OpenKedgePilotAgent --policy-name OpenKedgePilotAgentAccess
aws --profile "$AWS_PILOT_ADMIN_PROFILE" iam delete-role --role-name OpenKedgePilotExecution
aws --profile "$AWS_PILOT_ADMIN_PROFILE" iam delete-role --role-name OpenKedgePilotGateway
aws --profile "$AWS_PILOT_ADMIN_PROFILE" iam delete-role --role-name OpenKedgePilotAgent
echo 'Removed pilot roles. Terminate the disposable instance separately if it remains.'
