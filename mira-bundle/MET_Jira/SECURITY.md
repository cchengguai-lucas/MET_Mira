# Security

## Authorization

- Project reads use visibility-aware project and issue resolvers.
- Baseline publication, risk decisions, scenario preview, and apply require project-admin
  authorization.
- Apply accepts only a freshly recomputed server suggestion whose analysis token and issue
  lock version match; arbitrary dates and priorities are rejected.
- Agent conversations, messages, memories, feedback, and pending actions are user-owned.
- Agent tools reuse application authorization and ambiguous project context fails closed.
- Page navigation is available only after deterministic detection of an explicit navigation
  request and still requires user confirmation.

## Data and AI

- Live-data answers require a successful authorized read tool.
- Hidden projects are excluded from agent and JQL-assistant metadata.
- Durable memory is opt-in, visible to its owner, project-scoped where selected, and deletable.
- Agent history and tool output are bounded; audits redact likely credentials and email
  addresses.
- Write tools and navigation create pending actions and do not execute before confirmation.

## Notifications

PM alerts use stable risk fingerprints plus event versions and outbox dedupe keys. Staging
defaults to shadow mode, preventing external delivery while preserving analysis coverage.

Report suspected vulnerabilities privately to the repository maintainers. Do not include
production credentials or personal data in an issue or chat transcript.
