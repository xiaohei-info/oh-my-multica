---
name: software-development-karpathy-guidelines
description: Behavioral guidelines to reduce common LLM coding mistakes. Use when writing, reviewing, or refactoring code to avoid overcomplication, make surgical changes, surface assumptions, and define verifiable success criteria.
license: MIT
---

# Karpathy Guidelines

Behavioral guidelines to reduce common LLM coding mistakes, derived from [Andrej Karpathy's observations](https://x.com/karpathy/status/2015883857489522876) on LLM coding pitfalls.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 5. Answer the Question, Not the Implied Next Step

**Research questions are not implementation mandates.** When the user asks "does X", "will Y", "is Z", "how does", or "what would happen if" — that is not a request to change anything.

Common failure pattern:
- User: "Will these 40 tools waste my tokens?" → Agent: answers + modifies config
- User: "How does this repo work?" → Agent: clones and installs it
- User: "Clarify the mapping between current repo paths and design-doc terms" → Agent: starts bulk-renaming design docs instead of adding the requested bridge note

Special case for docs and repo-structure work:
- When the user asks for a README, orientation note, or terminology mapping, prefer the smallest bridging artifact that explains the relationship.
- Do NOT silently expand the scope into sweeping doc rewrites, mass renames, or terminology cleanup unless the user explicitly asked for that broader change.
- Preserve the distinction between design-layer terms (architecture roles like Agent Service / Agent Runtime) and physical filesystem names (e.g. app/ and hermes-agent/) when the user wants both to coexist.

Correct pattern:
- Answer the question with data.
- If there's a natural "would you like me to..." follow-up, offer it SEPARATELY after answering.
- Do not bundle the answer and the action in one turn unless explicitly asked.

Messaging/file-delivery pitfall:
- If the user asks you to **send a file** (e.g. "把文档发给我", "send me the markdown file"), they want an actual attachment or outbound file delivery — not the file contents pasted into chat.
- In that case, use the messaging/file-delivery path first. Do not substitute with an inline dump unless the user explicitly asks for the content.
- If your first attempt used a file marker/path but the user says they did not receive a file, switch to an explicit send step to the concrete channel/target and confirm delivery with the returned handle.

Ask yourself: "Did the user's message contain an imperative verb (do X, change Y, install Z)?" If no, answer only.

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Special case for runtime/docs/config tasks:
- When introducing a repo-local `.env` workflow, verify which entrypoints actually auto-load that file.
- Do not assume `python app.py` / `python server.py` behaves the same as wrapper scripts like `start.sh`, `ctl.sh`, or `bootstrap.py`.
- If only some entrypoints load `.env`, document the preferred paths explicitly and call out the direct-run exception.
- Prefer the smallest user-facing fix: add the local `.env`, wire docs to it, and avoid broad launcher refactors unless requested.
- If the user's goal is **single-source runtime control** (for example: "the Python in `.env` must be the one every app/test/server path uses"), audit all interpreter-discovery points before claiming the workflow is unified. Common drift points: test fixtures, config helpers, bootstrap launchers, subprocess fallbacks to `sys.executable`, `python3`, or a sibling tool's bundled venv.
- In that situation, treat silent fallback as a design bug, not convenience. Recommend fail-fast semantics: either use the explicit env-configured interpreter everywhere, or error clearly when it is missing/invalid.

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

