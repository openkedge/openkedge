package openkedge

import future.keywords.in

deny[decision] {
  input.intent.type == "ec2:TerminateInstances"
  some instance in input.context.instances
  instance.tags.critical == "true"
  decision := {
    "rule": "protect-production",
    "reason": "critical instance detected"
  }
}

deny[decision] {
  input.intent.type == "ec2:TerminateInstances"
  some instance in input.context.instances
  instance.tags.env == "prod"
  decision := {
    "rule": "protect-production",
    "reason": "production instance detected"
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
