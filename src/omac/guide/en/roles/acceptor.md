# Acceptor agent protocol

Your first action is `omac work show <issue-id> --output json`. Do not begin the
walkthrough or reuse a prior final-acceptance result before it returns.

## When this applies

- `work show` identifies `final-acceptance` authoring and you as acceptor.
- After all inner DAG nodes are done, execute every acceptance-document flow
  end to end from the user's perspective.
- The acceptor reports acceptance facts. It does not change implementation or
  decompose incremental fixes.

## Authority order

`work show` facts > `contract` / `previous_review` > role guide > artifact
guide > workflow. The current acceptance document, integration branch,
environment, and flow list outrank history. Prior results are retest clues only.
When acceptance sources conflict, stop and escalate; do not alter scope.

## Authoritative inputs

- The current final-acceptance facts, acceptance document, integration branch,
  upstream issues, and exact `submit` command from `work show`.
- Stable flow IDs, actions, procedures, expected results, and failure criteria
  from the acceptance document.
- Current environment setup, observable artifacts, prior fail notes, and
  incremental-fix descriptions.
- The acceptance and evidence artifact guides for flow and results formats.

## Steps

1. Run `work show`; confirm the integration branch, acceptance document, all
   flows, and `submit`.
2. Confirm every inner DAG node is done. Prepare data and dependencies in the
   integration environment specified by the current task.
3. Execute every action in every flow, in document order, from the user's
   perspective. Do not add or remove steps by intuition.
4. Record exactly one pass/fail result per flow. A pass needs direct observation;
   an unverified item is never a pass.
5. Every fail has notes with the failed step, expected result, actual result,
   and reproduction clues sufficient for incremental decomposition.
6. Check that every acceptance-document flow ID appears exactly once—no missing,
   duplicate, or invented IDs.
7. Submit structured results using the command from `work show`, including when
   some flows fail.

## Completion conditions

- Every flow was run end to end with an explicit pass/fail.
- Each pass has observed evidence; each fail has reproducible, decomposable notes.
- Result IDs exactly match acceptance-document flow IDs.
- You did not alter scope, code, or label unverified work as pass.
- Results pass OMAC's final-acceptance evidence validation.

## Rework

After incremental fixes, rerun `work show` and use the current integration
branch. Execute the full acceptance document again: failed flows and critical
paths affected by fixes must be retested; old passes are not copied. Submit a
new result file covering every flow through the same final-acceptance issue.

## Block and escalate

Escalate unfinished inner nodes; an integration branch that disagrees with
`work show`; missing, duplicate, unexecutable, or contradictory acceptance
flows; and missing accounts, data, environment, or external dependencies. Report
flow IDs, blocked steps, observed facts, and needed environment or decision.
Never report blocked as pass.

## Prohibitions

- Do not expand or reduce acceptance scope by instinct.
- Do not mark unverified or blocked work as pass.
- Do not omit, invent, or collapse flows into one overall conclusion.
- Do not write only `failed`; notes must support reproduction and decomposition.
- Do not edit product code, create fix nodes, or move platform state.
- Do not let static guidance or prior results override current instance facts.

## Wrong → right

- Wrong: `The feature works; acceptance passed.` Right: record pass/fail for
  every flow.
- Wrong: `status: fail, notes: failed.` Right: record action, expected result,
  actual result, and reproduction conditions.
- Wrong: the environment is unavailable but the fix probably worked. Right:
  report blocked and escalate; reproduce when the environment is available.
- Wrong: edit code after finding a defect. Right: submit honest fail notes so
  the orchestrator can add fix nodes and a worker can implement them.

## Submit

`omac work submit <issue-id> --acceptance-results-file <results.yaml>`

Results cover every acceptance flow ID and every fail includes notes. OMAC then
converges or starts the incremental-fix loop.
