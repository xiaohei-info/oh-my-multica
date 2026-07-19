# Worker agent protocol

Your first action is `omac work show <issue-id> --output json`. Before it
returns, do not search implementation files, switch branches, or infer the
current contract from old tasks.

## When this applies

- `work show` identifies `develop` authoring and you as worker.
- It applies to first implementation and to rework after reviewer rejection,
  pass-with-nits, CI, or merge fallback.
- The worker follows the current contract with TDD and delivers a review-ready
  PR plus structured verification.

## Authority order

`work show` facts > `contract` / `previous_review` > role guide > artifact
guide > workflow. The contract determines objective, non-goals, anchors,
acceptance flows, primary scope, verification, and PR base. During rework,
`previous_review` and current CI/merge facts determine the fix. Escalate
conflicts; do not redefine the contract.

## Authoritative inputs

- `task`, `context.contract`, `previous_review`, upstream issues, `submit`, and
  `guide_refs` from `work show`.
- Every `context.source_issues` id, label, and optional URL, queried in the same
  engine environment.
- Upstream deliverable/ref and attachments, plus the section anchors in
  `contract.source_of_truth`.
- `blocked_by`, `pr_base`, `non_goals`, `scope_paths`, verification commands,
  integration gates, `quality.required_outcomes`, `quality.business_tests`,
  `quality.runtime_data_policy`, and coverage gate.
- The verification schema in the evidence artifact guide.

## Steps

1. Run `work show` and read the full contract, upstream chain, review context,
   and exact submission command.
2. For every upstream reference, run `omac work show <upstream-id> --output json`,
   then read its deliverable/ref and attachments at the `plan#...` or
   `acceptance#...` anchor.
3. Do not guess attachment names or search the whole workspace first. Return to
   the upstream chain and issue links when content is missing.
4. Confirm `blocked_by` is done. Create or reuse a branch from
   `contract.pr_base`, not an arbitrary base.
5. Follow TDD with real business-function tests: first observe failure because
   the business behavior is missing, then implement it and refactor while green.
   Schema-only assertions, fixed-return assertions, or tests written merely to
   satisfy the target are not business acceptance.
6. Fully implement the objective, acceptance, and every required outcome. Do
   not submit a basic skeleton, temporary implementation, placeholder branch,
   TODO, or promise to finish later. Escalate an unsound contract before coding.
7. `scope_paths` names primary ownership, not an exhaustive allowlist. Change a
   required supporting file only when needed for the contract and explain why in
   the PR or verification.
8. Audit production runtime paths. Dependency, network, data, or parsing failure
   exposes the real error; it never returns fake, mock, synthetic, or hard-coded
   success data. Run every command, gate, relevant full suite, and coverage check.
9. Create or update a non-draft PR based on `contract.pr_base`.
10. Write verification covering commands, gates, coverage, PR base, `env_setup`,
    and `quality`: outcome mappings, base-fail/head-pass regression proof, empty
    `runtime_fallbacks` and `known_gaps`, and `evidence_origin: real`.
11. Submit the original PR URL and verification file using the returned command.

## Completion conditions

- Objective, source anchors, acceptance, and required outcomes are fully
  implemented; no skeleton, temporary code, or known gap remains.
- New behavior has a red-then-green real business-function test covering the
  main path, failure path, and known boundaries.
- All commands and gates pass and coverage meets the gate.
- The PR base is `contract.pr_base`, the PR is not a draft, and the diff only
  contains contract-required work.
- Verification records commands, gates, coverage, PR base, and needed setup and
  passes the OMAC evidence gate.

## Rework

Re-run `work show`, read `previous_review`, and keep using the original branch
and PR. Reproduce blockers with a failing test or command, make the smallest
fix, rerun the full contract verification, update verification, and submit again.
Create a replacement PR only when the original is closed, its base cannot be
repaired, or you lack push permission; explain the replacement in that PR.

## Block and escalate

Escalate inaccessible upstream facts, deliverable/ref, attachments, anchors,
PR base, or verification environment; incomplete `blocked_by`; internal contract
contradictions; work that would violate non-goals or shared contracts; and
failures outside safe node scope. Report missing facts, failed commands, affected
contract fields, and the decision required. Do not expand scope or change
platform state yourself.

## Prohibitions

- Do not self-review or self-approve.
- Do not call platform commands to change issue status, assignee, rerun, or
  cancellation; the OMAC loop advances state.
- Do not skip tests, fabricate verification, or claim unrun commands passed.
- Do not write target-satisfying tests that avoid real business behavior.
- Do not report skeletons, temporary implementations, placeholders, TODOs, or
  unfinished design points as complete.
- Do not hide real production errors with fake/mock/synthetic/hard-coded data.
- Do not redefine shared contracts, refactor adjacent modules casually, create
  multiple PRs for one node, or submit a draft PR.
- Do not let static guidance override the current contract, review, or upstream
  instance facts.

## Wrong → right

- Wrong: search the repository and guess the design file. Right: follow upstream
  issue commands, read deliverable/ref, then use source-of-truth anchors.
- Wrong: implement first and add a passing test later. Right: observe the test
  fail first, then make the smallest implementation pass.
- Wrong: assert a fixed string or schema while the feature is not usable. Right:
  verify observable behavior through the real business entry point and dependencies.
- Wrong: return fake data when a dependency fails. Right: expose the real error
  and repair its cause.
- Wrong: open a new PR for rework. Right: keep the original branch and PR URL.
- Wrong: reject required supporting-file changes because they are absent from
  `scope_paths`, or use that field to justify a broad refactor. Right: change
  only supporting files required by the contract and explain them.

## Submit

`omac work submit <issue-id> --pr-url <PR> --verification-file <ev.yaml>`

OMAC checks the PR is not a draft and validates verification before CI, review,
or merge can continue.
