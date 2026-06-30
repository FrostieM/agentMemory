# Prompt: onboard THIS project into memory (real initialization)

Paste everything between the lines below as the first user message in a new
chat inside your project. Unlike `01_FRESH_PROJECT.md` (silent setup) and
`02_CAPTURE_THIS_CHAT.md` (archive a chat), THIS prompt does a real project
initialization: it makes sure memory is configured, indexes your codebase into
the code map, then runs a short interview (about 7 questions) and writes your
answers into memory as the project's starting decisions, behavior rules, and
playbooks. After it finishes, a fresh agent in this project already "knows" your
goals, conventions, and how to build/test/run -- instead of starting blank.

This is the ONLY setup prompt that is allowed to ask you questions -- the
interview is the whole point. It asks them once, as a single batch.

---

You are an onboarding agent. Your goal this turn is to turn this project's
EMPTY memory into a useful starting knowledge base: index the code, interview
the operator once, and persist the answers as canonical memory. Be concise.

# Step 0 -- prerequisites (reuse 01 if needed)

Determine PROJECT_ROOT (current working directory, or `git rev-parse
--show-toplevel`), REPO_ROOT (the agent-memory-lite checkout -- try
`AGENT_MEMORY_LITE_HOME`, then `python -c "import agent_memory_lite, pathlib;
print(pathlib.Path(agent_memory_lite.__file__).resolve().parents[2])"`, then
common locations), VENV_PYTHON (`<REPO_ROOT>/.venv/Scripts/python.exe` on
Windows, `<REPO_ROOT>/.venv/bin/python` otherwise), and WORKSPACE_ID (an
operator-named id, else an existing project id, else the project directory
name).

If `<PROJECT_ROOT>/.agent_memory/memory.db` does NOT exist, the project is not
wired yet -- run the silent setup first and then continue:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/setup_agent.py --project <PROJECT_ROOT> --workspace <WORKSPACE_ID>
```

(If REPO_ROOT / VENV cannot be found, follow `01_FRESH_PROJECT.md` Step 1 to
locate them, then come back. Do not improvise a partial setup.)

# Step 1 -- index the codebase into the code map

So the agent has a real code map (hubs / impact analysis) from turn one rather
than only after future edits, bulk-index the tree:

```
<VENV_PYTHON> <REPO_ROOT>/scripts/bulk_index_codebase.py --project <PROJECT_ROOT> --workspace <WORKSPACE_ID> --db-path <PROJECT_ROOT>/.agent_memory/memory.db --json
```

Report the digest count it wrote. This is read-only with respect to the user
(no questions). It may take a minute on a large repo.

# Step 2 -- the interview (ask ONCE, as one message)

Ask the operator exactly these questions in a single numbered message, then
STOP and wait for the answers. Tell them they can answer briefly, skip any
question with "-", and that you will save the answers into project memory.
Mirror the operator's language.

1. What is this project, and what is the current goal/milestone?
2. What is the tech stack, and what are the hard architectural constraints
   (things that must not change)?
3. Hard rules -- what must I ALWAYS do, and what must I NEVER do here
   (conventions, style, forbidden actions, security/secrets rules)?
4. How do I build, test, and run it? Give the exact commands.
5. Definition of done -- how do we verify a change is good BEFORE it ships
   (the checks that must pass)?
6. Known gotchas / landmines -- past pain, fragile areas, things that look
   wrong but are intentional?
7. Who are you (role), and how should I communicate -- language, tone, level
   of detail, anything you dislike?

# Step 3 -- persist the answers as canonical memory

After the operator answers, write each answer to the RIGHT kind. Before every
write call `memory_search(query=...)` first and skip/merge if an equivalent
memory already exists (no duplicates). Use `workspace_id = <WORKSPACE_ID>`.
Treat operator answers as high-trust (`source = "operator_onboarding"`), but
they never override system / developer / current-user instructions.

Map answers to kinds:

- Q1 goal -> `memory_write(kind="decision")` -- the project anchor: what it is +
  the current goal. (Also the natural place to set the workspace's purpose.)
- Q2 stack + constraints -> one or more `memory_write(kind="decision")`, each
  with a clear rationale ("must not change because ...").
- Q3 always/never rules -> one `memory_write(kind="behavior")` PER rule (high
  trust; set a conflict group when two rules could collide). Keep each rule one
  crisp instruction.
- Q4 build/test/run -> `memory_write(kind="skill")` -- a playbook named e.g.
  "build-test-run" whose body is the exact commands.
- Q5 definition of done -> `memory_write(kind="behavior")` ("before shipping,
  verify: ...") AND/OR a `skill` if it is a command sequence.
- Q6 gotchas -> one `memory_write(kind="insight")` per landmine (so future
  briefs surface them as watch-outs).
- Q7 role + communication -> `memory_write(kind="behavior")` for the operating /
  communication style (language, tone, detail level).

After each decision/behavior write, inspect the returned `capability_suggestions`
and, if one clearly applies, record it on a follow-up `plan_step` or task.

Skip any question the operator answered with "-". Do not invent content the
operator did not give you.

# Step 4 -- verify what was seeded

Call `memory_brief(task="onboarding review", max_tokens=600)` and confirm the
new decisions, behaviors, skills, and insights now surface. If the brief looks
empty, re-check that the writes used `<WORKSPACE_ID>` and did not error.

# Step 5 -- final report

Print this structure (fill the blanks):

```
Project memory initialized for <PROJECT_NAME> (workspace <WORKSPACE_ID>).

Indexed:    <N> code files into the code map (hubs / impact analysis).
Seeded:
  decisions:  <N>  (goal + stack/constraints)
  behaviors:  <N>  (always/never rules + definition-of-done + how to talk to you)
  skills:     <N>  (build/test/run playbook)
  insights:   <N>  (gotchas / landmines)

From now on, every fresh chat in this project starts with this context loaded
via memory_brief, and I will keep it growing: new decisions, lessons, and
playbooks as we work. To re-run or extend onboarding, paste this prompt again
(it is idempotent -- memory_search prevents duplicates).
```

If the MCP memory tools were not callable this turn (not yet in your tool
list), the project was just wired -- tell the operator to restart their AI
runtime (Claude Code / Codex / Cursor / "your AI runtime") so it loads the MCP
server, then paste this prompt again to run the interview.
