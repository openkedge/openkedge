package openkedge

default allow = false

allow {
  input.intent.type == "ec2:DescribeInstances"
}

allow {
  input.intent.type == "ec2:TerminateInstances"
  count(deny) == 0
}

reasons[reason] {
  input.intent.type == "ec2:DescribeInstances"
  reason := "read-only instance discovery is allowed"
}

reasons[reason] {
  input.intent.type == "ec2:TerminateInstances"
  count(deny) == 0
  reason := "policy pack approved requested mutation"
}

matchedRules["safe-default"] {
  allow
}
