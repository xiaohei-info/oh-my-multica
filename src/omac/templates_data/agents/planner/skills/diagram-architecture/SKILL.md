---
name: diagram-architecture
description: "Dark-themed SVG architecture/cloud/infra diagrams as HTML."
version: 1.0.0
author: Cocoon AI (hello@cocoon-ai.com), ported by Hermes Agent
license: MIT
dependencies: []
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [architecture, diagrams, SVG, HTML, visualization, infrastructure, cloud]
    related_skills: [concept-diagrams, excalidraw]
---

# Architecture Diagram Skill

Generate professional, dark-themed technical architecture diagrams as standalone HTML files with inline SVG graphics. No external tools, no API keys, no rendering libraries — just write the HTML file and open it in a browser.

## Scope

**Best suited for:**
- Software system architecture (frontend / backend / database layers)
- Cloud infrastructure (VPC, regions, subnets, managed services)
- Microservice / service-mesh topology
- Database + API map, deployment diagrams
- Anything with a tech-infra subject that fits a dark, grid-backed aesthetic

**Look elsewhere first for:**
- Physics, chemistry, math, biology, or other scientific subjects
- Physical objects (vehicles, hardware, anatomy, cross-sections)
- Floor plans, narrative journeys, educational / textbook-style visuals
- Hand-drawn whiteboard sketches (consider `excalidraw`)
- Animated explainers (consider an animation skill)

If a more specialized skill is available for the subject, prefer that. If none fits, this skill can also serve as a general SVG diagram fallback — the output will just carry the dark tech aesthetic described below.

Based on [Cocoon AI's architecture-diagram-generator](https://github.com/Cocoon-AI/architecture-diagram-generator) (MIT).

## Workflow

1. User describes their system architecture (components, connections, technologies)
2. Generate the HTML file following the design system below
3. Save with `write_file` to a `.html` file (e.g. `~/architecture-diagram.html`)
4. User opens in any browser — works offline, no dependencies

### Output Location

Save diagrams to a user-specified path, or default to the current working directory:
```
./[project-name]-architecture.html
```

### Preview

After saving, suggest the user open it:
```bash
# macOS
open ./my-architecture.html
# Linux
xdg-open ./my-architecture.html
```

## Design System & Visual Language

### Color Palette (Semantic Mapping)

Use specific `rgba` fills and hex strokes to categorize components:

| Component Type | Fill (rgba) | Stroke (Hex) |
| :--- | :--- | :--- |
| **Frontend** | `rgba(8, 51, 68, 0.4)` | `#22d3ee` (cyan-400) |
| **Backend** | `rgba(6, 78, 59, 0.4)` | `#34d399` (emerald-400) |
| **Database** | `rgba(76, 29, 149, 0.4)` | `#a78bfa` (violet-400) |
| **AWS/Cloud** | `rgba(120, 53, 15, 0.3)` | `#fbbf24` (amber-400) |
| **Security** | `rgba(136, 19, 55, 0.4)` | `#fb7185` (rose-400) |
| **Message Bus** | `rgba(251, 146, 60, 0.3)` | `#fb923c` (orange-400) |
| **External** | `rgba(30, 41, 59, 0.5)` | `#94a3b8` (slate-400) |

### Typography & Background
- **Font:** JetBrains Mono (Monospace), loaded from Google Fonts
- **Sizes:** 12px (Names), 9px (Sublabels), 8px (Annotations), 7px (Tiny labels)
- **Background:** Slate-950 (`#020617`) with a subtle 40px grid pattern

```svg
<!-- Background Grid Pattern -->
<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
  <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1e293b" stroke-width="0.5"/>
</pattern>
```

## Technical Implementation Details

### Component Rendering
Components are rounded rectangles (`rx="6"`) with 1.5px strokes. To prevent arrows from showing through semi-transparent fills, use a **double-rect masking technique**:
1. Draw an opaque background rect (`#0f172a`)
2. Draw the semi-transparent styled rect on top

### Connection Rules
- **Z-Order:** Draw arrows *early* in the SVG (after the grid) so they render behind component boxes
- **Arrowheads:** Defined via SVG markers
- **Security Flows:** Use dashed lines in rose color (`#fb7185`)
- **Boundaries:**
  - *Security Groups:* Dashed (`4,4`), rose color
  - *Regions:* Large dashed (`8,4`), amber color, `rx="12"`

### Spacing & Layout Logic
- **Standard Height:** 60px (Services); 80-120px (Large components)
- **Vertical Gap:** Minimum 40px between components
- **Message Buses:** Must be placed *in the gap* between services, not overlapping them
- **Legend Placement:** **CRITICAL.** Must be placed outside all boundary boxes. Calculate the lowest Y-coordinate of all boundaries and place the legend at least 20px below it.

## Document Structure

The generated HTML file follows a four-part layout:
1. **Header:** Title with a pulsing dot indicator and subtitle
2. **Main SVG:** The diagram contained within a rounded border card
3. **Summary Cards:** A grid of three cards below the diagram for high-level details
4. **Footer:** Minimal metadata

### Mandatory Visual QA Before Handoff

Do **not** stop at XML validity or "the file opens". For architecture/design diagrams, perform a human-legibility pass before declaring success.

Required checks:
- No legend, note, or annotation box may cover layer titles, actor boxes, or primary nodes.
- External actors (for example `User`, `Telegram`, `Admin`) must have clear whitespace from the nearest system box; connector lines should occupy a channel, not visually merge with node borders.
- Labels on arrows must not sit on top of boxes or boundary strokes.
- Boundary boxes must leave safe space for their titles and internal nodes.
- If a diagram is embedded into Obsidian/SVG-first docs, verify the layout visually, not only by parsing the SVG.
- For layered business/solution diagrams, prefer **orthogonal / right-angle routing** over diagonal shortcuts whenever a line would otherwise cross or visually disappear into a component box.
- If a connector passes behind semi-transparent cards, confirm the path is still legible; if not, reroute it through a dedicated gutter/channel instead of relying on z-order.
- After content revisions change the chosen base platform or core terminology, update **all coupled assets together**: the inline SVG, the standalone HTML wrapper, and the surrounding document captions/legend text.
- **Do not trust SVG `<text>` to wrap automatically.** When a label or description is close to box width, split it manually with `<tspan>` lines or enlarge the box/canvas. Any text that overflows or gets bottom-clipped counts as a failed handoff.
- When the deliverable lives in Markdown/Obsidian, prefer **referencing the SVG asset** (for example `![[resources/foo.svg]]`) plus a separate HTML wrapper, instead of pasting large raw SVG blocks into the `.md`, unless inline SVG is explicitly required.
- If you read SVG/HTML through tools that inject line numbers (for example `read_file` style `N|content` output), **never write that output back directly**. Strip numbering first or rebuild from the clean asset, otherwise line-number prefixes will corrupt the document.

Common failure pattern to avoid:
- fixing only syntax/CDATA/XML issues
- but leaving spatial collisions such as actor boxes touching adapters, legends covering top layers, or labels crowding the main path
- or copying tool-decorated file output back into the real artifact, which pollutes `.md` / `.svg` / `.html` with line-number prefixes

When collisions are found, prefer **re-layout of the whole zone** over tiny local nudges. Typical fixes:
- move the full system boundary, not just one conflicting node
- create a dedicated whitespace channel between external actors and first-layer adapters
- enlarge canvas height before pushing legends into content
- split dense views into top-level + detail diagrams instead of stacking everything tighter
- convert long one-line labels into two or three `tspan` lines before shrinking the font further

For a concrete Obsidian/SVG sync cleanup example, see `references/obsidian-svg-sync-pitfalls.md`.

### Standalone HTML portability rule

When the output is expected to be opened directly from `file://`, attached into Obsidian, or shared as a single self-contained HTML artifact, keep the SVG **inline inside the HTML**.

Do **not** replace a working inline SVG with `<img src="sibling.svg">` or another relative-resource wrapper unless the user explicitly wants a coupled multi-file package and the loading context is controlled. Local note apps and direct file opening commonly succeed at loading the HTML shell while failing to resolve the sibling asset, which degrades into a broken-image placeholder.

Related editing pitfall:
- if you inspected SVG/HTML/Markdown through a numbered file-reader tool, never paste that numbered output back into the source without stripping prefixes first; otherwise line-number markers can be written into the actual asset.

### Info Card Pattern
```html
<div class="card">
  <div class="card-header">
    <div class="card-dot cyan"></div>
    <h3>Title</h3>
  </div>
  <ul>
    <li>• Item one</li>
    <li>• Item two</li>
  </ul>
</div>
```

## Output Requirements
- **Single File:** One self-contained `.html` file
- **No External Dependencies:** All CSS and SVG must be inline (except Google Fonts)
- **No JavaScript:** Use pure CSS for any animations (like pulsing dots)
- **Compatibility:** Must render correctly in any modern web browser
- **Local-file portability:** If the user will open the diagram from Obsidian, Finder/Explorer, or a `file://` path, keep the HTML wrapper **self-contained with inline `<svg>`**. Do **not** replace the diagram body with `<img src="relative.svg">`, `<object data="...svg">`, or other relative-resource wrappers unless the user explicitly prefers split assets and you have verified that environment resolves sibling files correctly.

## Pitfalls

### Pitfall: Turning a working inline-SVG diagram into a broken-image HTML wrapper

**表现**: You generate a valid HTML diagram, then later "simplify" it by replacing the inline SVG with `<img src="diagram.svg">` so markdown, SVG, and HTML share one source file.

**后果**:
- local-file opening in Obsidian or browser `file://` mode can show a broken image placeholder or `ERR_FILE_NOT_FOUND`
- the HTML stops being a true standalone artifact
- the user sees an empty wrapper even though the SVG file exists

**正确做法**:
1. Treat the standalone HTML as a self-contained deliverable.
2. If you also need a `.svg` companion for markdown embedding, keep **both** assets: a standalone inline-SVG HTML and a sibling raw SVG.
3. Do not swap the HTML body to external `<img src="...svg">` unless you have a verified reason and verified rendering path.
4. When the user reports a broken placeholder in the HTML wrapper, first inspect whether the wrapper references a sibling SVG instead of embedding it inline.

## Template Reference

Load the full HTML template for the exact structure, CSS, and SVG component examples:

```
skill_view(name="architecture-diagram", file_path="templates/template.html")
```

The template contains working examples of every component type (frontend, backend, database, cloud, security), arrow styles (standard, dashed, curved), security groups, region boundaries, and the legend — use it as your structural reference when generating diagrams.

