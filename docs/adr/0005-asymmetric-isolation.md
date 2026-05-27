# ADR 0005: Asymmetric workspace isolation вЂ” reads loose, writes strict

Status: accepted (2026-05-01)

## Context

After ADR 0004 introduced hub mode, the user articulated a contract for
how an AI agent should be allowed to touch other projects' memory:

> "On request the agent may read another project's memory, but only
> when explicitly asked. Casual reading or any writing into a foreign
> memory must never happen on its own."

The first version of strict isolation
(`MEMORY_STRICT_WORKSPACE_ISOLATION=true`) was symmetric: any
cross-workspace request вЂ” read or write вЂ” was rejected. That was too
strict in practice: when working in project A, asking the agent
"show me copyBot memory" was a legitimate explicit request that the
guard rejected, forcing the user to open a different chat.

Three options:

- **A. Symmetric strict.** What we had. Honest but friction-heavy. The
  user has to switch chat contexts to read.

- **B. No strict at all.** Hub mode everywhere. Maximum flexibility,
  zero accident protection. An agent could pollute another project's
  audit log without the user noticing.

- **C. Asymmetric.** Reads loose, writes strict. An agent in project A
  can read project B when explicitly asked, but cannot write into
  project B no matter how the user phrases the request вЂ” for that, the
  user has to open a chat in B (or in the parent dir for hub mode).

## Decision

Adopt option C. The user's "explicit request" maps to two different
semantics depending on direction:

- A *read* request ("look at copyBot decisions") is satisfied
  in-place вЂ” the agent calls `memory_brief / memory_search(workspace_id="copyBot")`
  from its current chat. The result is reference material; nothing
  lands in the calling chat's audit log.

- A *write* request ("save this episode in copyBot") is rejected
  in-place. The agent must explain that writes from a project chat are
  blocked, and either ask the user to open a chat in copyBot or to
  open a hub chat. This makes "I'm pretending I'm helping you save
  this in copyBot" social engineering attempts impossible вЂ” the guard
  blocks the call regardless of what the agent thinks the user meant.

Implementation splits the existing single guard
(`ensure_workspace_allowed`) into two:

- `ensure_workspace_readable(workspace_id, settings)` вЂ” enforces only
  `forbid_default_workspace`. Reads to any non-default workspace are
  allowed.

- `ensure_workspace_writable(workspace_id, settings)` вЂ” enforces
  `forbid_default_workspace` AND `strict_workspace_isolation` (when
  `hub_mode` is off). Writes to non-anchor workspaces raise
  `ValidationError: writes to workspace_id='X' are blocked by
  MEMORY_STRICT_WORKSPACE_ISOLATION`.

`ensure_workspace_allowed` is kept as a backwards-compatible alias for
the write guard (the safer of the two, so legacy callers default to
the stricter behavior).

Every HTTP route is reclassified:

- **Read routes** (`search`, `brief`, `get`, `plan`,
  `list_research_agenda`, `list_agent_capabilities`,
  `list_capability_links`, `list_behavior_instructions`,
  `list_maintenance_events`, `hygiene_report`, `quality_gate`, UI
  state and SSE) call `ensure_workspace_readable`.

- **Write routes** (every `ingest_*`, `write_*`, `update_*`, `upsert_*`,
  `add_*`, `register_*`, `link_*`, `distill_*`, `promote_*`,
  `reject_*`, `resolve_*`, `record_*`, `compact`, `run_evals`) call
  `ensure_workspace_writable`.

The MCP stdio server's `_with_workspace` and `_workspace_from_args`
take an `intent` parameter. Read handlers (12 of them) pass
`intent="read"`; write handlers default to `intent="write"`.

Hub mode (`MEMORY_HUB_MODE=true`) bypasses strict isolation for both
reads and writes вЂ” the operator chose a shared service, both
directions are explicit.

`forbid_default_workspace` is independent of read/write split: a
service in project mode rejects `workspace_id="default"` for any
request, because that fallback is almost always a bug.

## Consequences

Positive:

- The user-visible contract matches the user's stated requirement:
  reads on request, no foreign writes.
- Cross-workspace reads work in-place. No need to switch chats just to
  glance at another project's decisions or theories.
- Foreign writes always fail with the same clear error. No silent
  pollution of another project's audit log.

Negative:

- Two guards instead of one. Routes have to be classified. Mitigated
  by keeping `ensure_workspace_allowed` as the write alias so any
  unclassified caller defaults to the safer behavior.

- The asymmetry surprises some users who expect symmetric isolation.
  Documented in `README.md` Getting Started, `AGENT_CONTRACT.md`
  Project mode vs hub mode, and the `AGENT_SETUP/` prompts.

- A foreign read can leak rendered memory into the calling chat's LLM
  context (which is then potentially in the calling chat's audit log
  *as a quoted block*, not as a structured memory write). This is a
  social-engineering vector: "summarize copyBot decisions" в†’ context
  is fetched в†’ the agent's response gets ingested as an episode in the
  calling project. We mitigate by recommending agents not echo foreign
  workspace content into their own audit log; the strict guard cannot
  enforce this because the agent's own response handling is
  out-of-band of the memory service. ADR-level acknowledged risk.
