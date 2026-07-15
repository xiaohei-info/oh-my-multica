# Document Canvas / Interactive HTML Ecosystem (2026-05)

Research on GitHub tools and projects that convert Markdown or structured documents into interactive single-page HTML.

## Most Relevant

### Microsoft Chartifact ⭐75
- **Repo**: `microsoft/chartifact`
- **What**: Declarative document format → interactive data-driven pages (reports, dashboards, presentations)
- **Features**: Markdown + JSON dual authoring, Vega charts, Mermaid diagrams, sortable tables, sliders/dropdowns, reactive variables, REST data sources, export standalone HTML
- **Strengths**: Designed for LLM generation, sandboxed runtime, VS Code extension, web viewer
- **Limitations**: Data/dashboard-oriented, not ideal for pure text/knowledge documents
- **Best for**: MD with data, tables, charts → interactive dashboard HTML

### LangChain Open Canvas ⭐5.4k (ARCHIVED 2026-02)
- **Repo**: `langchain-ai/open-canvas`
- **What**: Chat + document editing canvas, inspired by OpenAI Canvas
- **Features**: Markdown doc editing, version history, reflection memory agents
- **Limitations**: Archived/unmaintained; editor not converter; no direct MD→HTML
- **Best for**: Reference architecture, not direct use

### mayfer/open-artifacts ⭐213
- **Repo**: `mayfer/open-artifacts`
- **What**: Single HTML file for prompting LLM and running generated code in iframe
- **Features**: JSX + npm via esbuild-wasm in-browser bundling, zero deployment
- **Limitations**: Runs LLM-generated code, doesn't convert MD to interactive page
- **Best for**: Artifact prototype sandbox

## Supporting Tools

### BlueprintLabIO/markdown-ui
- Markdown with embedded interactive UI components (buttons, forms), React/Vue/Svelte rendering
- Good for: making Markdown LLM output interactive

### LiaScript
- Markdown dialect → interactive courses (quizzes, animations, code execution)
- Good for: educational content

### Docsify-This (`hibbitts-design/docsify-this`)
- MD files → shareable web pages with embed/presentation modes
- Good for: lightweight MD rendering, minimal interactivity

### ikenga-artifact-builder ⭐2 (Royalti-io)
- Claude Code skill: teaches agent to output single-file HTML artifacts instead of markdown
- Install: `npx skills add Royalti-io/ikenga-artifact-builder`
- Not a standalone tool; a prompt guide for Claude Code

## Gap Assessment

No existing tool does "structured MD knowledge document → Canvas-style interactive HTML page" automatically. The highest-leverage approach is a skill (like `claude-design` Document Canvas Mode) that guides an LLM to read the MD, map its structure to interactive components, and generate a self-contained HTML file.

