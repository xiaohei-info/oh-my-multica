# Document-to-Interactive-HTML: External Tool Landscape

Research date: 2026-05-19

## The Gap

`claude-design` handles "design from scratch" well, but converting an existing Markdown document into a polished, interactive single-page HTML (à la Gemini NotebookLM Canvas) is a distinct class of work. No single open-source tool fully automates this today.

## Key External Projects

### Microsoft Chartifact ⭐75 — `microsoft/chartifact`
- Declarative doc format (Markdown or JSON) → interactive data-driven HTML pages
- Components: Vega charts, Mermaid diagrams, sortable tables, sliders/dropdowns, reactive variables, REST data sources
- Export to standalone HTML; sandboxed runtime; designed for LLM generation
- Best for: data-heavy docs, dashboards, reports with charts
- Weak for: pure knowledge/narrative docs needing rich navigation, search, collapsible sections

### LangChain Open Canvas ⭐5.4k — `langchain-ai/open-canvas` (ARCHIVED 2026-02)
- Chat + document editing canvas, inspired by OpenAI Canvas
- Markdown document editing with versioning, reflection agents
- Full-stack app (not a converter); archived and unmaintained

### mayfer/open-artifacts ⭐213
- Single HTML file: prompt any LLM → run generated code in iframe
- esbuild-wasm for browser-side JSX/npm bundling
- Tool for running LLM-generated code, not for converting MD

### BlueprintLabIO/markdown-ui
- Embed interactive UI components (buttons, forms) in Markdown
- React/Vue/Svelte renderers
- Good for LLM streaming output with interactive widgets

### LiaScript
- Markdown dialect → full interactive courses (quizzes, animations, code execution)
- Education-focused, strongest interactivity but oriented toward courseware

### Docsify-This — `hibbitts-design/docsify-this`
- MD file → shareable web page, embeddable, presentation mode
- Lightest option; weak interactivity (pure render)

### ikenga-artifact-builder ⭐2 — `Royalti-io/ikenga-artifact-builder`
- Claude Code skill: teach agent to output single-file HTML artifacts instead of markdown
- Install: `npx skills add Royalti-io/ikenga-artifact-builder`
- Not Hermes-compatible format; concept is portable though

## Recommended Approach for Hermes

When a user wants "MD doc → interactive HTML canvas page":

1. Read the MD content fully
2. Classify the doc type:
   - **Data-driven** (tables, metrics, charts) → Chartifact-style layout with Vega/Mermaid
   - **Narrative/knowledge** (concepts, sections, hierarchy) → Canvas-style with nav, collapsible sections, search, TOC
   - **Hybrid** → blend both patterns
3. Use `claude-design` process with these document-specific patterns:
   - Sticky sidebar TOC with scroll-spy
   - Collapsible sections (`<details>/<summary>` or custom accordion)
   - In-page search/filter
   - Dark/light theme toggle
   - Key terms highlighted with tooltip definitions
   - Mermaid diagrams for relationships
   - Smooth scroll + reading progress indicator
4. Output single self-contained HTML per `claude-design` artifact rules

