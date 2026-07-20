# OMAC workflow (Controller Agent)

This guide is for the Controller Agent that starts, advances, and recovers an
OMAC workflow. It explains stable mechanisms only; it does not replace the
facts of a specific issue. For a dispatched task, first run:

```bash
omac work show <issue-id> --output json
```

## When to use this guide

- Start the design, acceptance, decomposition, and development loop from a
  request.
- Continue an existing manifest DAG.
- Decide whether the next action is `plan`, `dag`, or recovery.

## Authority order

Resolve conflicts in this order:

1. Current facts returned by `work show`.
2. The current task's `contract` or `previous_review`.
3. The relevant role guide.
4. The relevant artifact guide.
5. This workflow overview.

## Standard path

1. Run `omac init` to configure the engine, workspace, project, and role map.
2. Run `omac plan create --name <feature> [--goal <request> | --doc <design-document>]`
   to produce the design document, acceptance document, and manifest DAG.
3. Run `omac dag run .omac/<feature>.yaml` in the foreground until it
   converges or returns exit 20.
4. After exit 20, inspect with `omac dag status` and `omac node show`, make an
   explicit choice with `omac node retry|accept|abandon`, then run
   `omac dag run` again.

## From plan to dag run

`omac plan create/resume` exit 0 means that planning, acceptance definition,
and decomposition have converged and the manifest was written. Do not guess
the filename. Read `manifest:` and `Next: omac dag run ...` in command output.
The Controller Agent runs that next command directly so `dag run` owns
development, CI, review, merge, and final acceptance.

## Stage navigation

| Stage | Role guide | Artifact guide |
|---|---|---|
| Design | `omac guide role planner` | `omac guide artifact design` |
| Acceptance definition | `omac guide role planner` | `omac guide artifact acceptance` |
| DAG decomposition | `omac guide role orchestrator` | `omac guide artifact manifest` |
| Development | `omac guide role worker` | `omac guide artifact evidence` |
| Independent review | `omac guide role reviewer` | Reviewed artifact + `omac guide artifact evidence` |
| Final acceptance | `omac guide role acceptor` | `omac guide artifact acceptance` + `evidence` |
| Recovery | `omac guide recovery` | - |

For a dispatched task, do not pre-read every guide. Read `guide_refs` from
`work show` and load only the topics required for the current task.

## Stable mechanisms

- One issue carries one complete task. Authoring, review, and rework stay on
  the same timeline.
- An issue titled `[DAG:...]` was dispatched by OMAC. The agent runs
  `omac work show <issue-id> --output json` first, then uses the returned
  `submit` command.
- A downstream issue gives humans links to upstream issues. Agents read
  `work show.context.source_issues`, then inspect the referenced task context
  and deliverable/ref.
- Review is a phase of every issue type, not a separate issue.
- The acceptance document anchors the requested outcome. A manifest node's
  `contract.acceptance` must reference an acceptance flow.
- Workers, reviewers, and acceptors submit structured evidence. `omac work
  submit` rejects missing required evidence immediately.
- Workers map every acceptance item to a concrete business test through
  `business_tests` on a successful command; Reviewers inspect those tests for
  real business behavior.
- Reviewers continue after findings, complete the entire review scope, set
  `full_review_completed: true`, and report every blocker and nit found in one
  review pass.
- State exists in both the manifest and the platform work item. Re-running
  `dag run` reuses completed nodes and continues from current state.

## Supervision boundary

`omac dag run` is a foreground, blocking process. Do not send it to the
background or claim that work is still being supervised when no foreground run
is active. Either run it until it returns before reporting, or state plainly
that no supervision process is running.

## Completion conditions

- After `plan create/resume` returns exit 0, the next command shown in output
  has been run.
- `dag run` returns exit 0 and every manifest node is in an allowed terminal
  state.
- If a command returns exit 20, the workflow has entered `omac guide recovery`;
  do not report it as complete.

## Recognizing the entry point

- Title starts with `[DAG:...]`: treat it as an OMAC task instance.
- No prefix: treat it as an ordinary issue unless its body explicitly requests
  an OMAC command.

Run `omac guide` to list topics. Do not invent a topic or submit arguments from
memory.
