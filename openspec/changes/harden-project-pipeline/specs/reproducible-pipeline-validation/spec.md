## ADDED Requirements

### Requirement: Declarative Python environment
The repository SHALL declare supported Python versions, runtime dependencies, development dependencies, and test configuration in version-controlled project metadata.

#### Scenario: Install in a clean environment
- **WHEN** a developer or CI runner installs the declared development environment
- **THEN** PyYAML, pypinyin, pytest, the agent SDK, and configured quality tools are available without undocumented manual installation

### Requirement: Single local quality command
The repository SHALL provide one documented command that runs syntax validation, unit tests, and static lint checks with a non-zero exit status on any failure.

#### Scenario: Quality checks pass
- **WHEN** all tracked Python files compile, all tests pass, and lint rules pass
- **THEN** the quality command exits zero

#### Scenario: A required dependency is missing
- **WHEN** the quality command runs in an environment that does not satisfy declared dependencies
- **THEN** it fails with an actionable installation message rather than silently skipping affected tests

### Requirement: Continuous integration gate
The default branch SHALL run the repository quality command in CI for every pull request and relevant push.

#### Scenario: Pull request introduces a regression
- **WHEN** a pull request causes syntax, test, or lint failure
- **THEN** the CI check fails and exposes the failing command output

### Requirement: Hermetic pipeline fixtures
Automated tests MUST cover control-plane execution against minimal Node and Python target repositories without accessing real GitHub, SMTP, credential stores, or model services.

#### Scenario: Node target fixture
- **WHEN** the standard executor and dispatch tests run against a minimal Node repository fixture
- **THEN** they verify cwd isolation, test evidence, branch naming, and publication gating through fakes

#### Scenario: Python target fixture
- **WHEN** the same tests run against a minimal Python repository fixture
- **THEN** they verify runtime PATH injection, test command execution, and publication gating through fakes

#### Scenario: External dependency failure
- **WHEN** mocked GitHub or Git commands time out, fail, or return invalid JSON
- **THEN** tests verify the corresponding `UNKNOWN` and non-destructive blocked behavior
