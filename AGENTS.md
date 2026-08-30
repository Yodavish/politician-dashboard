# AGENTS.md

## Project Scope

The initial system will:

- Collect publicly available politician stock trade disclosures.
- Run the ingestion process on a daily schedule.
- Parse and normalize the source data.
- Store the resulting records in PostgreSQL.
- Expose the stored data through an API.
- Provide a web dashboard for exploring and analyzing the data.

The initial implementation should focus on data ingestion, storage,
API access, and dashboard functionality.

Future analytical features, including investment-related scoring or
buy/sell assessments, are out of scope unless explicitly approved.

## Current State

The repository may initially be empty. Do not assume a framework,
language, database library, frontend framework, or other tooling unless
it has been established in the approved project architecture.

Follow the approved architecture and technology choices provided by the
developer.

## Engineering Principles

- Prefer simple solutions over unnecessary abstraction.
- Implement the smallest reasonable solution for each phase.
- Keep components reasonably separated and maintainable.
- Avoid premature optimization.
- Avoid unnecessary infrastructure and dependencies.
- Do not introduce dependencies without explaining why they are needed.
- Preserve working functionality when modifying existing code.
- Favor readable, maintainable code over clever solutions.
- Use established project conventions consistently.

## Agent Rules

- Do not commit or push Git changes unless explicitly asked.
- Do not delete files or directories without approval.
- Do not perform destructive database operations without approval.
- Do not modify secrets, credentials, or production configuration.
- Do not expose secrets in source code, logs, tests, or documentation.
- Do not change the approved architecture without explaining the reason
  and receiving approval.
- Do not introduce major frameworks, infrastructure, or dependencies
  without approval.
- Do not rewrite working code unnecessarily.

## Development Process

For each implementation phase:

1. Explain what you intend to implement.
2. Identify important technical decisions and assumptions.
3. Identify anything that is unclear or requires developer input.
4. Implement the smallest reasonable change.
5. Run relevant tests, validation, and linting when available.
6. Review the resulting changes for unintended modifications.
7. Report:
   - Files changed
   - What was implemented
   - Tests and validation performed
   - Important technical decisions
   - Assumptions made
   - Known limitations or remaining concerns

Do not begin a major implementation phase until the developer has
approved the proposed approach.

## Database

- Use database migrations for schema changes.
- Do not modify the database schema manually when a migration should be
  created.
- Do not run destructive migrations against production databases.
- Keep development data and production data separate.
- Validate ingested data before storing it when practical.

## Testing

- Write tests for important business logic and data-processing logic.
- Test data parsing and normalization carefully.
- Add integration tests where database or API behavior needs validation.
- Run relevant tests after making changes.
- Do not remove or weaken tests simply to make them pass.

## Git

- Do not initialize a Git repository unless explicitly asked.
- Do not create commits unless explicitly asked.
- Do not push changes unless explicitly asked.
- Keep changes organized into logical, reviewable units.

## Learning

When implementing a significant piece of functionality, explain the
important engineering concepts involved.

Do not hide significant architectural decisions behind implementation
details.

If there are multiple reasonable approaches, briefly explain the
tradeoffs and recommend one.

## Ambiguity

If requirements are ambiguous and the decision could materially affect
the architecture, data model, security, or long-term maintainability,
ask for clarification before implementing.

For minor implementation details, use reasonable engineering judgment
and document the assumption.

## Approval Required

Stop and ask for developer approval before:

- Changing the approved architecture.
- Changing the database schema design significantly.
- Introducing a major framework or infrastructure component.
- Adding a significant external service.
- Performing destructive operations.
- Removing existing functionality.
- Changing security boundaries.
- Expanding the scope of the current phase.
- Making a decision that could create significant technical debt.

## Dependencies

- Prefer the standard library when it provides a reasonable solution.
- Prefer established, actively maintained libraries when a dependency is justified.
- Before adding a dependency, explain its purpose and alternatives considered.
- Avoid adding dependencies solely for convenience when the functionality is small and straightforward.
- Keep dependencies scoped to the component that requires them.

## Security

- Never hardcode credentials, API keys, tokens, or passwords.
- Never commit secrets to Git.
- Validate and sanitize external input.
- Do not expose internal errors, credentials, database details, or stack traces through public API responses.
- Use least-privilege database credentials where practical.
- Treat external data as untrusted input.

## Data Integrity

- Treat source data as authoritative input and preserve the original source information where practical.
- Do not silently discard, overwrite, or alter source data during ingestion.
- Normalize data in a reproducible and documented manner.
- Handle missing, ambiguous, or malformed data explicitly.
- Do not invent or infer transaction information that is not supported by the source data.
- Preserve source references and timestamps for ingested records where practical.
- Make ingestion operations safe to re-run without unintentionally creating duplicate records.

## Scope Control

- Implement only the functionality requested for the current phase.
- Do not add unrelated features, refactors, or enhancements without approval.
- Do not expand the project's scope based solely on potential future requirements.
- Future ideas should be documented separately rather than implemented prematurely.

## Scheduled Ingestion

- Daily ingestion must be safe to run repeatedly.
- A failed ingestion run should not corrupt previously stored data.
- The system should provide enough logging to determine whether an
  ingestion run succeeded, partially succeeded, or failed.
- Ingestion failures should be observable and diagnosable.
- Do not silently treat failed or incomplete source retrieval as a
  successful ingestion.