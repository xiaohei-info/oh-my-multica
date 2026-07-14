# Architecture vs Flowchart Smells

Use this note when a supposed 技术架构图 keeps getting read as a 流程图.

## What a technical architecture diagram should answer first

A viewer should first understand:
1. system boundary
2. owned planes / domains / containers
3. stable technical units inside each plane
4. external dependencies
5. what kinds of relations exist between units

If the viewer first understands only `request enters -> logic runs -> result returns`, the picture is likely a flowchart.

## Common smells that make a 技术架构图 look like a flowchart

### 1. Linear spine dominates the page
Symptoms:
- one obvious top-to-bottom or left-to-right chain
- every box is placed on that chain
- secondary relations look like branches off a main storyline

Rewrite:
- place containers / planes first
- group technical units by ownership and role
- demote the single request path to one of several relations, not the whole composition

### 2. Boxes are named like actions, not components
Symptoms:
- labels read like verbs or steps: `receive`, `parse`, `dispatch`, `return`, `merge now`
- boxes feel like moments in time rather than stable runtime units

Rewrite:
- rename toward stable units: `surface adapter`, `ingress router`, `task repository`, `worker runtime`, `delivery controller`

### 3. Arrows imply sequence instead of relation type
Symptoms:
- every arrow looks the same
- viewer reads `A then B then C`
- no difference between data path, control path, governance path, cache/refresh path

Rewrite:
- define 2-4 line semantics max
- usually enough: main data/read-write path, control/backend call path, governance/observability path
- label arrows by relation meaning, not step number

### 4. External systems sit inline with internal modules
Symptoms:
- user, channel, CLI, storage, future runtime all appear on the same main chain
- owned and unowned components are visually flattened

Rewrite:
- move external actors/dependencies outside the system boundary
- let boundary ownership tell part of the story before arrows do

### 5. Planes are missing or too weak
Symptoms:
- the diagram has many boxes but no strong domain grouping
- colors exist but do not correspond to meaningful ownership/plane boundaries

Rewrite:
- use a small number of explicit planes/containers
- examples: `surface`, `control`, `contract`, `dispatch`, `execution`, `truth/governance`

## Recommended repair sequence

1. Delete most arrows temporarily.
2. Draw the system boundary.
3. Place external actors/dependencies outside the boundary.
4. Draw planes/containers inside the boundary.
5. Place stable technical units inside those planes.
6. Re-add only the minimum relation types needed.
7. Ask: does the eye first read planes and ownership, or a storyline?
8. If storyline still dominates, move that storyline into a separate 核心流程图.

## Pairing rule

In detailed design, it is normal to ship both:
- one **技术架构图** for static runtime structure
- one **核心流程图 / 时序图** for dynamic behavior

Do not force one image to do both jobs equally.

