# Source Map — business-solution-architecture-lifecycle

## Purpose

This note records what doctrine was pulled into the architect-private skill `business-solution-architecture-lifecycle`, with a source-audited goal of preserving the original drawing methodology details. The core drawing doctrine is retained in full, while some presentation structure may be expanded by architect-specific overlays.

## Primary sources

- Original draft drawing skill:
  - `/home/ubuntu/.hermes/cache/documents/doc_b4012a6c95cb_业务解决方案架构图skill.md`
- Inbox lifecycle note:
  - `/var/lib/syncthing/Obsidian/Guantik/Inbox/能力全景图/专业技术能力/软件架构设计的生命周期.md`

## Preservation rule applied

The private architect skill was rewritten to keep the full drawing doctrine from the draft as the baseline layer, including:
- business-context parsing
- architecture-mode selection
- layered narrative structure
- domain color mapping
- semantic node vocabulary
- relationship/edge mapping
- layout constants and blacklists
- tool selection strategy
- user interaction/output contract
- quick reference cards
- example scene description

These remain part of the core skill. Where architect overlays expand the presentation structure, the original baseline is preserved explicitly rather than replaced.

## Inbox doctrines added on top

The Inbox lifecycle note contributed the architect-stage additions:
- solution-design stage placement
- dependency reliability / degrade / fallback / isolation questions
- state-closure review
- risk/governance review
- implementation milestone / minimum closed-loop framing

## Additional local doctrine integrated

### architect/SOUL.md
Added local profile-specific constraints:
- verify path before implementation path
- keep system-level artifacts abstract
- state boundaries, risks, and verification explicitly
- avoid stuffing module-level details into top-level design artifacts

### architecture-lifecycle-delivery
Added top-level architect chain alignment:
- this skill is a specialist method inside solution design
- diagram responsibility must stay separated from technical/deployment views
- risk should alter structure early rather than appear only in appendices

## Deliberately omitted from the skill core

Still omitted because they belong to later stages or sibling skills:
- detailed technical architecture expansion
- project/code/module structure design
- data table / ER-level detail
- interface field-level definition
- deployment topology and ops SOP specifics

## Why this is profile-private

This skill is not only a drawing recipe. It now also encodes architect-specific review judgment about:
- lifecycle placement discipline
- stage-gate review logic
- system-level abstraction control
- risk/dependency/state/milestone framing before technical design

