# Manifest Sync And Dependency Links Design

## Goal

Keep the remote DAG manifest current when GitHub merges advance `main`, and make
each develop issue expose its direct DAG predecessor issues to humans and agents.

## Manifest Sync

`commit_manifest` continues to own only the manifest file. It first creates the
local manifest commit and tries the normal push. If the push is rejected because
the remote branch advanced, it fetches the configured upstream, verifies every
local-only commit touches only the manifest path, rebases those commits onto the
upstream, and retries the push once.

The retry must not rewrite unrelated local work. If local-only commits touch other
paths, the rebase conflicts, or the retry fails, synchronization remains failed and
the existing warning identifies the exact step. A rebase conflict is aborted so
the repository is not left in an in-progress rebase.

## Direct Dependency Links

When dispatching a develop node, OMAC keeps the plan, acceptance, and decomposition
issues from `manifest.meta.source_issues`. It then appends one source reference for
each direct `blocked_by` node that has a `work_item_id`.

The reference label is `前置开发任务 · <node title or node key>`. `blocked_by`
continues to contain DAG node keys for graph evaluation; only `source_refs` contains
platform issue IDs. Transitive ancestors are not expanded because agents can follow
the chain recursively through `omac work show`.

## Current Run Repair

Pause only the OMAC supervisor, rebase the local manifest-only commit onto the latest
remote `main`, push it, and restart the supervisor. Existing Agent runs continue.
Backfill AITEAM-774 and AITEAM-775 with AITEAM-773 as their direct predecessor in
their issue body and `source_refs` without adding a comment or triggering a run.

## Verification

- A remote-only commit followed by a local manifest change auto-rebases and pushes.
- Unrelated local-only commits are never rebased automatically.
- A manifest conflict aborts cleanly without overwriting remote state.
- Develop issues include direct predecessor issue links for one or multiple deps.
- Nodes without dependencies or predecessor work item IDs retain existing behavior.

