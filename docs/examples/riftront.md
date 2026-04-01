# Riftront Example

Concrete example: Bakery Scenario.

- **Owner** proposes `operating_status = open`.
- **Agent** proposes `operating_status = closed`.

## Policy Resolution
Because the Owner has higher authority, the agent's conflicting proposal is rejected.

## Result
Before: `operating_status = uninitialized`
After: `operating_status = open`
