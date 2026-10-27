## ADDED Requirements

### Requirement: Independent verification test scope
For Python target repos, `independent_verify` SHALL select the replayed test command by priority: the project profile's declared `verify_test_cmd` first, then the dev-reported test command, and skip (with an explanatory note) only when neither exists. The verification result note SHALL record which source supplied the command (`profile-declared` / `dev-reported`), so audits can distinguish the coverage basis of a green result. This priority applies without weakening ADR-0002 project autonomy: a profile-declared command is the project's own explicit full-scope expression, not an orchestrator guess.

#### Scenario: Profile-declared command takes priority
- **WHEN** the target repo is Python, the profile declares `verify_test_cmd`, and the dev also reported a test command
- **THEN** `independent_verify` runs the profile-declared command, and the note records `profile-declared`

#### Scenario: Fallback to dev-reported command
- **WHEN** the target repo is Python, the profile declares no `verify_test_cmd`, and the dev reported a test command
- **THEN** `independent_verify` replays the dev-reported command, and the note records `dev-reported` (behavior identical to before this change)

#### Scenario: Neither source exists
- **WHEN** the target repo is Python, the profile declares no `verify_test_cmd`, and the dev reported none
- **THEN** `independent_verify` skips the test with an explanatory note (behavior identical to before this change)

#### Scenario: Node repos are unchanged
- **WHEN** the target repo has a `package.json`
- **THEN** `independent_verify` runs `npm ci` + `npm test` exactly as before this change, regardless of profile or dev-reported commands
