package openkedge

deny[decision] {
  input.intent.type == "ec2:TerminateInstances"
  input.blastRadius.riskLevel == "CRITICAL"
  decision := {
    "rule": "blast-radius-limit",
    "reason": "blast radius exceeds threshold"
  }
}

reasons[reason] {
  decision := deny[_]
  reason := decision.reason
}

matchedRules[rule] {
  decision := deny[_]
  rule := decision.rule
}
