# Prompt 1: initialize a NEW project into memory

Use this for a fresh / greenfield project whose memory is empty. Paste
everything between the lines below as the first user message in a new chat
inside your project. The agent wires memory if needed, indexes whatever code
exists, runs a short ~7-question interview, and writes your answers into memory
as the project's starting decisions, behavior rules, and build/test/run
playbook. It asks its questions once, as a single batch.

(For a project that has already been worked on -- existing code, git history,
docs -- use `02_EXISTING_PROJECT.md` instead: it does all of this AND mines the
existing codebase/context into memory on its own.)

---

You are a project-initialization agent. Your goal this turn is to turn this
project's EMPTY memory into a useful starting knowledge base: ensure memory is
wired, index the code, interview the operator ONCE, and persist the answers as
canonical memory. Be concise.

# Step 0 -- locate the pieces

- PROJECT_ROOT = current working directory (or `git rev-parse --show-toplevel`).
- REPO_ROOT = the agent-memory-lite checkout. Try in order: the
  `AGENT_MEMORY_LITE_HOME` env var; then
  `python -c "import agent_memory_lite, pathlib; print(pathlib.Path(agent_memory_lite.__file__).resolve().parents[2])"`;
  then common locations (`~/agent-memory-lite`, `~/work/agent-memory-lite`,
  `~/code/...`, `~/projects/...`, on Windows also `~/Desktop/work/...`); then a
  bounded `find ~ -maxdepth 5 -name agent-memory-lite -type d`. If it cannot be
  found, stop and ask the operator for the absolute path -- do not improvise a
  partial setup.
- VENV_PYTHON = `<REPO_ROOT>/.venv/Scripts/python.exe` (Windows) or
  `<REPO_ROOT>/.venv/bin/python` (macOS/Linux); else the `python` on PATH (note
  the deviation).
- WORKSPACE_ID = an operator-named id, else an existing project id, else the
  project directory name.

# Step 1 -- ensure memory is wired

If `<PROJECT_ROOT>/.agent_memory/memory.db` does NOT exist, wire it:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/setup_agent.py --project <PROJECT_ROOT> --workspace <WORKSPACE_ID>
```

This creates the project DB + applies migrations, seeds only the neutral
discipline behaviors (no project knowledge), writes the MCP entry into
`<PROJECT_ROOT>/.claude/settings.json`, drops the contract into `CLAUDE.md` /
`AGENTS.md`, installs the brief/pre-tool/post-edit hooks, and smoke-tests the
MCP server. It is idempotent. If it reports a missing prerequisite, follow its
`[!]` hints (`pip install -e ".[dev,mcp]"` from REPO_ROOT; Ollama is optional --
leave `OLLAMA_PROBE_SKIP=true` if it is unreachable; never install Ollama
yourself).

If the memory tools are not yet in your tool list after wiring, the runtime has
not reloaded its MCP config -- tell the operator to restart their AI runtime
(Claude Code / Codex / Cursor / "your AI runtime") and then paste this prompt
again. Do not continue blind.

# Step 2 -- index whatever code exists

```
<VENV_PYTHON> <REPO_ROOT>/scripts/bulk_index_codebase.py --project <PROJECT_ROOT> --workspace <WORKSPACE_ID> --db-path <PROJECT_ROOT>/.agent_memory/memory.db --json
```

Report the digest count (a brand-new project may be near zero -- fine).

# Step 3 -- the interview (ask ONCE, as one message)

Ask the operator exactly these in a single numbered message, then STOP and wait.
Tell them they can answer briefly, skip any with "-", and that you will save the
answers into project memory. Mirror the operator's language.

1. What is this project, and what is the current goal/milestone?
2. What is the tech stack, and what are the hard architectural constraints
   (things that must not change)?
3. Hard rules -- what must I ALWAYS do, and what must I NEVER do here
   (conventions, style, forbidden actions, security/secrets rules)?
4. How do I build, test, and run it? Give the exact commands.
5. Definition of done -- how do we verify a change is good BEFORE it ships?
6. Known gotchas / landmines -- fragile areas, things that look wrong but are
   intentional?
7. Who are you (role), and how should I communicate -- language, tone, level of
   detail, anything you dislike?

# Step 4 -- persist the answers to the RIGHT kind (roles / skills / playbooks too)

Before EVERY write call `memory_search(query=...)` first and skip/merge if an
equivalent memory exists (no duplicates). Use `workspace_id = <WORKSPACE_ID>` in
every payload. Operator answers are HIGH trust but never override system /
developer / current-user instructions. Mark provenance with the field each kind
actually supports: a `behavior` takes `source_type="operator_onboarding"` (the
field is `source_type`, NOT `source`); other kinds have no source field, so note
provenance in `rationale` / `summary`. Skip any "-" answer; do not invent content
the operator did not give. After each decision/behavior write, inspect
`capability_suggestions` and record a clearly applicable one on a plan step.

Map each answer to its kind. ROLES, SKILLS, and PLAYBOOKS are all
`kind="skill"`, told apart by `subtype` -- get this right or a role is not a
role. For every capability write set `subtype`, a one-line `summary`, a
`when_to_use_short`, and put the FULL detail in `body_md` (markdown -- this is
what the agent reads back via `memory_invoke_skill`); optionally also fill the
structured `*_json` fields (JSON-encoded lists, exact column names below) for
filtering and linking.

- Q1 goal -> `kind="decision"` (fields: `title`, `decision_text`, `rationale`) --
  the project anchor (what it is + current goal).
- Q2 stack + each hard constraint -> `kind="decision"` (one per constraint, with
  a `rationale`: "must not change because ...").
- Q3 each ALWAYS / NEVER rule -> `kind="behavior"`, REQUIRED fields `name` +
  `rule`; optional `rationale`, `applies_to` (list), `conflict_group` (when two
  rules can clash), `source_type="operator_onboarding"`. One write per rule.
- Q4 build / test / run -> `kind="skill", subtype="playbook"`, name
  "build-test-run". Put the exact commands in `body_md`; optionally
  `steps_json`, `success_criteria_json`, `triggers_json`, `required_skills_json`.
- Q5 definition of done -> `kind="skill", subtype="playbook"` ("verify-before-ship",
  the checks as `steps_json` / `success_criteria_json`) AND a short
  `kind="behavior"` ("before shipping, verify: ...") so it also shapes behaviour.
- Q6 each gotcha / landmine -> `kind="insight"`, REQUIRED `summary` +
  `insight_type` (e.g. "lesson") + `status="candidate"`.
- Q7 your ROLE -> `kind="skill", subtype="role"`. `body_md` = purpose +
  responsibilities + boundaries; optionally `responsibilities_json`,
  `boundaries_json`, `handoff_triggers_json`, `tools_json`, `related_roles_json`.
- Q7 communication style (language / tone / detail) -> `kind="behavior"`.
- Any reusable technique the operator describes -> `kind="skill", subtype="skill"`
  (optionally `when_to_use_json`, `inputs_json`, `outputs_json`, `tools_json`).
- Any project-specific term / jargon worth pinning -> `kind="concept"`
  (`name`, `definition`, `concept_kind`).

Two CORRECT example writes (verified payload shapes):

```
# a ROLE (kind=skill + subtype=role; substance in body_md; no "source" field)
memory_write(kind="skill", payload={
  "workspace_id": "<WORKSPACE_ID>", "subtype": "role",
  "name": "backend-engineer",
  "summary": "Owns the API + DB layer of this project",
  "when_to_use_short": "server-side / schema / migration work",
  "body_md": "## Purpose\n<...>\n## Responsibilities\n- <...>\n## Boundaries\n- <...>",
  "responsibilities_json": "[\"design APIs\", \"guard migrations\"]",
  "boundaries_json": "[\"no UI work\"]"
})

# a BEHAVIOR rule (name + rule REQUIRED; provenance is source_type)
memory_write(kind="behavior", payload={
  "workspace_id": "<WORKSPACE_ID>",
  "name": "tests-before-commit",
  "rule": "Always run `pytest -q` and the linters before committing.",
  "rationale": "operator onboarding answer",
  "applies_to": ["git commit"], "conflict_group": "ci",
  "source_type": "operator_onboarding"
})
```

A playbook uses `"subtype": "playbook"` with `steps_json` / `success_criteria_json`;
a plain skill uses `"subtype": "skill"` with `inputs_json` / `outputs_json`.

# Step 5 -- verify + report

Call `memory_brief(task="onboarding review", max_tokens=600)` and confirm the new
memories surface. Then print:

```
Project memory initialized for <PROJECT_NAME> (workspace <WORKSPACE_ID>).

indexed:    <N> code files into the code map
seeded:     decisions=<N>  behaviors=<N>  roles=<N>  skills=<N>  playbooks=<N>  insights=<N>  concepts=<N>
mcp tools:  <verified | restart your runtime to load them>

From now on every fresh chat in this project starts with this context via
memory_brief, and I keep it growing as we work. Re-run this prompt any time --
it is idempotent (memory_search prevents duplicates).
```
