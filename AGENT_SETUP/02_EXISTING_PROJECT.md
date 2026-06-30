# Prompt 2: initialize an EXISTING project into memory

Use this for a project that has already been worked on -- existing code, git
history, docs, maybe prior AI-agent sessions -- but whose memory is empty or
thin. Paste everything between the lines below as the first user message in a
new chat inside your project. The agent does everything `01_NEW_PROJECT.md` does
(wire memory, index the code, interview you, seed your answers) AND, on top of
that, mines the existing project ON ITS OWN -- reading the code map, manifests,
docs, and git history -- and writes what it infers into memory, clearly marked
as agent-inferred so your answers always win.

---

You are a project-initialization agent for an ALREADY-DEVELOPED project. Your
goal this turn: wire memory, index the code, autonomously mine the existing
project into memory, interview the operator ONCE, and persist both -- with
operator answers taking precedence over anything you inferred. Be concise.

# Step 0 -- locate the pieces

- PROJECT_ROOT = current working directory (or `git rev-parse --show-toplevel`).
- REPO_ROOT = the agent-memory-lite checkout. Try in order: the
  `AGENT_MEMORY_LITE_HOME` env var; then
  `python -c "import agent_memory_lite, pathlib; print(pathlib.Path(agent_memory_lite.__file__).resolve().parents[2])"`;
  then common locations (`~/agent-memory-lite`, `~/work/...`, `~/code/...`,
  `~/projects/...`, on Windows also `~/Desktop/work/...`); then a bounded
  `find ~ -maxdepth 5 -name agent-memory-lite -type d`. If it cannot be found,
  stop and ask the operator for the absolute path -- do not improvise.
- VENV_PYTHON = `<REPO_ROOT>/.venv/Scripts/python.exe` (Windows) or
  `<REPO_ROOT>/.venv/bin/python` (Unix); else `python` on PATH (note it).
- WORKSPACE_ID = an operator-named id, else an existing project id, else the
  project directory name.

# Step 1 -- ensure memory is wired

If `<PROJECT_ROOT>/.agent_memory/memory.db` does NOT exist, wire it:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/setup_agent.py --project <PROJECT_ROOT> --workspace <WORKSPACE_ID>
```

It creates the DB + migrations, seeds only the neutral discipline behaviors,
writes the MCP entry into `.claude/settings.json`, drops the contract into
`CLAUDE.md` / `AGENTS.md`, installs the hooks, and smoke-tests MCP. Idempotent.
Follow any `[!]` hints (`pip install -e ".[dev,mcp]"`; Ollama optional, leave
`OLLAMA_PROBE_SKIP=true` if unreachable; never install Ollama yourself).

If the memory tools are not yet in your tool list, the runtime has not reloaded
its MCP config -- tell the operator to restart their AI runtime and paste this
prompt again. Do not continue blind.

# Step 2 -- index the existing codebase

```
<VENV_PYTHON> <REPO_ROOT>/scripts/bulk_index_codebase.py --project <PROJECT_ROOT> --workspace <WORKSPACE_ID> --db-path <PROJECT_ROOT>/.agent_memory/memory.db --json
```

Report the digest count. This builds the code map (hubs / impact) you will mine
in Step 3.

# Step 3 -- AUTONOMOUSLY mine the existing project (no questions yet)

Read what already exists and write what you can infer. This is the part that
makes an existing project different from a new one. Keep it BOUNDED (about 5-10
inferred memories total -- the highest-signal facts, not an exhaustive dump).
Before each write call `memory_search(query=...)` to avoid duplicates.

Sources to read and what to infer:
- `README*`, `docs/`, `ARCHITECTURE*`, `CONTRIBUTING*` -> the project's PURPOSE
  and high-level architecture.
- Dependency manifests (`pyproject.toml` / `package.json` / `go.mod` /
  `Cargo.toml` / `requirements*.txt` / `pom.xml` ...) -> the TECH STACK + build
  tooling.
- The code map you just built -- the top "hubs" (most-depended-on files). Get
  them via `memory_search(kinds=["code_digest"], query="hub core entry point")`
  or `memory_status` -> the KEY MODULES.
- `git log --oneline -50` and the most-frequently-changed files
  (`git log --name-only --pretty=format: | sort | uniq -c | sort -rn | head`)
  -> ACTIVE AREAS and recurring themes.
- Lint/format/test/CI config (`.ruff.toml`, `.eslintrc`, `pytest.ini`,
  `.github/workflows/`, `Makefile`, test directory layout) -> detected
  CONVENTIONS and how the project is built/tested.
- Any existing `CLAUDE.md` / `AGENTS.md` / `.cursorrules` that the OPERATOR
  wrote (ignore the agent-memory-lite contract block itself) -> existing rules.
- If real work already happened in THIS chat, a faithful summary of it.

Write each inference to the right kind, and MARK IT as agent-inferred so the
operator can review and it never masquerades as ground truth:
- Descriptive facts (purpose, stack, key modules, active areas) -> prefer
  `kind="insight"` (REQUIRED `summary` + `insight_type` e.g. "lesson" +
  `status="candidate"`) -- it also accepts `source="agent_inference"` +
  `trust_level="agent_observed"` and is the natural "observation" kind. If you
  write an inferred `kind="decision"` instead, mark it in its `rationale`
  ("agent-inferred, unconfirmed") with low `confidence` -- a `decision` REJECTS a
  `source` field, so do not pass one.
- Operator-authored rules found in an existing `CLAUDE.md`/`.cursorrules` ->
  `memory_write(kind="behavior")` (these ARE operator intent -> normal trust).
- Conventions YOU guessed from config (not explicitly stated) -> behavior
  CANDIDATES only / low trust, flagged for review -- do NOT auto-activate a rule
  you merely inferred (the contract forbids promoting un-reviewed inferred
  instructions to active high-trust behavior).
- A build / test / run procedure you detected from CI / Makefile / scripts ->
  `memory_write(kind="skill", subtype="playbook")` named "build-test-run"
  (the commands in `body_md`, plus `steps_json`), agent-inferred / low trust --
  the interview will confirm or correct it.
- A summary of this chat's work, if any -> `memory_write(kind="episode")`.

Do NOT fabricate a ROLE from code alone -- the role comes from the operator in
the interview (Step 4). Mine descriptive facts and a draft playbook only.

# Step 4 -- the interview (ask ONCE, as one message)

Now confirm/correct what you inferred. Ask these in ONE numbered message, then
STOP and wait. Tell the operator they can answer briefly, skip any with "-", and
that their answers OVERRIDE anything you inferred in Step 3. Mirror their
language. Where you already inferred an answer, show it and ask them to confirm
or fix it (e.g. "I see the stack is X / the build is `make test` -- correct?").

1. What is this project, and the current goal/milestone? (I inferred: <...>)
2. Tech stack + hard constraints that must not change? (I inferred: <...>)
3. Hard rules -- what must I ALWAYS / NEVER do (conventions, style, forbidden
   actions, secrets)?
4. Exact build / test / run commands? (I inferred: <...>)
5. Definition of done -- what must pass BEFORE a change ships?
6. Known gotchas / landmines (things that look wrong but are intentional)?
7. Who are you (role), and how should I communicate (language, tone, detail)?

# Step 5 -- persist the operator's answers (they win)

For each answer, `memory_search` first (dedup), then write to the RIGHT kind at
HIGH trust. Mark operator provenance with the field each kind supports: a
`behavior` takes `source_type="operator_onboarding"` (NOT `source`); a `decision`
REJECTS a `source` field, so note provenance in its `rationale`; skills have no
source field. ROLES, SKILLS, and PLAYBOOKS are all `kind="skill"` told apart by
`subtype` -- get this right or a role is not a role. For every capability write set `subtype`, a one-line `summary`, a
`when_to_use_short`, and the full detail in `body_md`; optionally fill the
structured `*_json` fields (JSON-encoded lists) for filtering/links.

- Q1 goal -> `kind="decision"` (`title`, `decision_text`, `rationale`). Q2 stack +
  each constraint -> `kind="decision"` (with `rationale`).
- Q3 each ALWAYS / NEVER rule -> `kind="behavior"`, REQUIRED `name` + `rule`;
  optional `rationale`, `applies_to`, `conflict_group`,
  `source_type="operator_onboarding"` (the field is `source_type`, NOT `source`).
- Q4 build / test / run -> `kind="skill", subtype="playbook"` "build-test-run"
  (commands in `body_md`; `steps_json`, `success_criteria_json`).
- Q5 definition of done -> `kind="skill", subtype="playbook"` ("verify-before-ship")
  AND a short `kind="behavior"` ("before shipping, verify: ...").
- Q6 each gotcha -> `kind="insight"` (REQUIRED `summary` + `insight_type` e.g.
  "lesson" + `status="candidate"`).
- Q7 your ROLE -> `kind="skill", subtype="role"` (`body_md` = purpose +
  responsibilities + boundaries; optionally `responsibilities_json`,
  `boundaries_json`, `handoff_triggers_json`, `tools_json`). Communication style ->
  `kind="behavior"`.
- Any reusable technique -> `kind="skill", subtype="skill"` (`inputs_json`,
  `outputs_json`, `tools_json`). Any domain term -> `kind="concept"` (`name`,
  `definition`, `concept_kind`).

Example role write (kind=skill + subtype=role; substance in body_md; skills have
no "source" field):
`memory_write(kind="skill", payload={"workspace_id":"<WORKSPACE_ID>",
"subtype":"role","name":"backend-engineer","summary":"Owns the API + DB layer",
"when_to_use_short":"server-side / schema work","body_md":"## Purpose ... ##
Responsibilities ... ## Boundaries ...","responsibilities_json":"[\"design APIs\"]"})`
A behavior rule uses `name` + `rule` (both required) +
`source_type="operator_onboarding"`.

When an operator answer CONTRADICTS something you inferred in Step 3, supersede
the inferred memory (`memory_edit`, or archive the inferred one and write the
operator's as canonical) so there is one consistent truth. Promote any Step-3
behavior CANDIDATE or draft playbook the operator confirmed to active. Inspect
`capability_suggestions` after decision / behavior writes.

# Step 6 -- verify + report

Call `memory_brief(task="onboarding review", max_tokens=700)` and confirm both
the inferred and the operator-confirmed memories surface. Then print:

```
Existing project memory initialized for <PROJECT_NAME> (workspace <WORKSPACE_ID>).

indexed:         <N> code files into the code map
agent-inferred:  decisions=<N> insights=<N> behavior-candidates=<N> draft-playbooks=<N> episodes=<N>
operator-seeded: decisions=<N> behaviors=<N> roles=<N> skills=<N> playbooks=<N> insights=<N> concepts=<N>
mcp tools:       <verified | restart your runtime to load them>

I mined the existing code, docs, and git history into memory and then confirmed
the key facts with you -- your answers superseded anything I guessed. Every
fresh chat now starts with this context via memory_brief, and I keep it growing
as we work. Re-run this prompt any time (idempotent -- memory_search dedups).
```
