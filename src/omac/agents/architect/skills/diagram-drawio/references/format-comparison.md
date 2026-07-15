# Diagram Format Comparison

When generating architecture diagrams, choose among the available structured, presentation-oriented, and whiteboard-style capabilities. Use this reference to select the right format for the situation.

## Quick Decision Table

| Factor | drawio-skill | architecture-diagram | excalidraw |
|--------|-------------|---------------------|------------|
| **Output format** | `.drawio` XML + PNG/SVG/PDF export | Self-contained `.html` (inline SVG) | `.excalidraw` JSON |
| **Visual style** | Classic enterprise, light bg, rounded rects | Dark tech, grid background, semi-transparent fills | Hand-drawn / whiteboard |
| **Obsidian embed** | `![[file.png]]` works directly | Cannot preview HTML inline; needs browser | Needs Excalidraw Obsidian plugin |
| **Desktop editing** | draw.io app (full editor) | Browser only; no WYSIWYG editor | excalidraw.com or Obsidian plugin |
| **Collaboration** | Share .drawio file | Share HTML file or host statically | excalidraw.com live collaboration |
| **Best for** | Formal docs, print, iterative refinement | Presentations, tech shares, web display | Brainstorming, team review, quick iteration |
| **CLI export** | draw.io desktop CLI (headless OK with xvfb) | None needed (HTML is the deliverable) | None needed (JSON is the deliverable) |
| **Vision self-check** | Export PNG, read with vision API | Screenshot HTML, read with vision API | No native rendering; upload to excalidraw.com for preview |

## When to use each

### drawio-skill (default for formal work)
- User needs Obsidian-embeddable output
- User will iterate on the diagram in draw.io desktop
- User needs PNG/SVG/PDF exports with embedded source (`-e` flag)
- Diagram has swimlanes, UML, ERD, or ML model figures
- Need the richest shape vocabulary (AWS icons, cylinders, diamonds, etc.)

### architecture-diagram (presentation / tech-share)
- User wants a dark, modern, visually striking diagram
- Output is for slides, web pages, or screen sharing
- Cloud infrastructure with VPC/region boundary boxes
- User explicitly wants a dark theme
- Obsidian embedding is NOT required (or user will screenshot for Obsidian)

### excalidraw (brainstorming / collaboration)
- User wants a hand-drawn / whiteboard aesthetic
- Team will collaborate live on excalidraw.com
- Diagram is a rough sketch or early-stage concept
- User has the Obsidian Excalidraw plugin installed
- User explicitly asks for Excalidraw format

## Multi-format workflow

When a user wants to compare styles or see alternatives for the same architecture:
1. Start with `drawio-skill` (most structured, self-check loop, Obsidian-ready)
2. Generate `architecture-diagram` HTML for the dark-tech variant
3. Generate `excalidraw` JSON for the hand-drawn variant
4. Present a brief comparison of tradeoffs (Obsidian compat, editability, visual style)
5. Let the user pick the one they want to keep iterating on

## Obsidian compatibility notes

- **draw.io PNG**: Directly embeddable via `![[file.png]]`. Co-locate `.drawio` source alongside for future edits.
- **architecture-diagram HTML**: Not previewable in Obsidian. Screenshot the browser render and embed the screenshot if Obsidian visibility is needed. The HTML file itself can be opened from the vault via `file://` link or an `app://` obsidian link.
- **Excalidraw JSON**: Requires the Obsidian Excalidraw community plugin. Without it, the `.excalidraw` file is just raw JSON. Alternative: export from excalidraw.com as SVG/PNG and embed that instead.
