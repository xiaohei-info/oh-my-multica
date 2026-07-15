# Terminology Convergence Across Asset Layers

Use this note when a solution-design task inherits mixed naming from BRD/PRD/prototypes/market copy.

## Core rule

Do not treat all assets as having the same naming obligation.

Split the asset set into layers first:

1. **Formal technical/architecture assets**
   - business solution documents
   - architecture review outputs
   - business-solution architecture diagrams
   - system / technical architecture diagrams

2. **Product-facing assets**
   - PRD narrative
   - demo/prototype copy
   - market-facing feature names
   - UX labels intentionally using branded or memorable wording

## Default convergence policy

- In **formal technical/architecture assets**, converge productized nicknames into responsibility-oriented technical terms.
- In **product-facing assets**, keep the product name when it still serves positioning, comprehension, or UX storytelling.
- Do not partially converge only one formal artifact. If the formal document is updated, update its linked formal diagram assets in the same pass.
- Do not automatically rewrite PRD/prototype/demo assets unless the user asks for full cross-layer unification.

## Example pattern

A product codename like:
- “龙虾协作协议”

may converge in formal technical assets to:
- 多智能体协作编排协议
- 协作编排执行层
- 协作编排判断层
- 多智能体委托协作

while remaining unchanged in:
- PRD feature labels
- demo animation titles
- prototype UI copy

when those product artifacts are still intentionally expressing the product concept rather than internal module boundaries.

## Why this matters

Without an explicit boundary, agents often make one of two mistakes:

1. **Under-normalization**
   - only the prose body changes, but diagrams/examples still carry the old codename
   - review readers conclude the solution is not actually converged

2. **Over-normalization**
   - PRD/demo/product copy is rewritten into dry technical naming even though the task only asked to clean technical design assets
   - product storytelling and prototype readability are damaged without real architecture benefit

## Review checklist

- Did the formal solution doc define the terminology boundary explicitly?
- Do the linked formal diagrams use the same technical term set as the doc body?
- Are any remaining product terms confined to product/prototype/market artifacts by intent rather than by omission?
- Did the editor avoid broad renaming outside the asked-for asset scope?

