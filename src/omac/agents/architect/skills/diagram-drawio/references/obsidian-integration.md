# Obsidian Integration Patterns

Patterns for generating draw.io diagrams that live inside an Obsidian vault, embedded in markdown notes.

## Embed Workflow

1. Identify the target note path in the vault (use `search_files` or `read_file`)
2. Generate `.drawio` XML to a temp location (e.g. `/tmp/`)
3. Export PNG to the same temp location (no `-e` for preview, `-e` for final)
4. Copy both `.drawio` and `.png` to the target note's directory
5. Patch or append the note with `![[filename.png]]` wikilink + legend section

**Key rule:** Obsidian uses `![[file.png]]` wikilink embeds, NOT markdown `![](path)`. The wikilink resolves against the vault root and renders inline.

## Architecture-from-Doc Pattern

When the user asks you to redraw an architecture diagram from an existing design document:

1. **Read the full document** — extract the module list (§6-style sections), layer definitions, and data-flow edges. Do not guess from section titles alone.
2. **Map layers to rows** — TB layout with one row per architectural layer. Add dashed horizontal separator lines between layers with rotated text labels in the left margin.
3. **Separate control-plane objects** — registries, specs, and contracts should be placed to the left of the main flow, connected with dashed "read" arrows. This prevents them from cluttering the primary data path.
4. **Separate ops/secondary paths** — watchdog, notifications, spec-onboarding loops, DLQ go to the right side, outside the main flow column.
5. **Place storage centrally** — cylinder shapes for databases sit below the processing row, centered under their respective writers.

## Cloud-Platform Color Conventions

For cross-cloud architecture diagrams, use the standard palette to indicate which platform each component runs on:

| Platform | fillColor | strokeColor | Color name | Typical components |
|----------|-----------|-------------|------------|-------------------|
| OCI | `#dae8fc` | `#6c8ebf` | Blue | Collector, Registry, Specs, Contracts, MySQL HeatWave, Watchdog, Notifications |
| AWS | `#fff2cc` | `#d6b656` | Yellow | SQS, Step Functions, Lambda, DLQ |
| GCP | `#d5e8d4` | `#82b366` | Green | BigQuery, GCS |
| Cloudflare | `#ffe6cc` | `#d79b00` | Orange | Workers, Pages, KV |
| External | `#f5f5f5` | `#666666` | Grey | External sources, consumers |

## Layer Separator Pattern

Use dashed horizontal lines between architectural layers with rotated labels in the left margin:

```xml
<!-- Layer separator line -->
<mxCell id="lsep1" value="" style="endArrow=none;dashed=1;html=1;strokeColor=#CCCCCC;strokeWidth=1;" edge="1" parent="1">
  <mxGeometry relative="1" as="geometry">
    <mxPoint x="0" y="130" as="sourcePoint" />
    <mxPoint x="1200" y="130" as="targetPoint" />
  </mxGeometry>
</mxCell>

<!-- Rotated layer label -->
<mxCell id="ll1" value="采集层" style="text;html=1;fontSize=11;fontStyle=1;align=center;verticalAlign=middle;rotation=-90;fontColor=#6c8ebf;" vertex="1" parent="1">
  <mxGeometry x="-35" y="160" width="80" height="30" as="geometry" />
</mxCell>
```

Adjust `x` offset for the label to keep it in the left margin without overlapping shapes.

## Legend Block

Always add a color-key legend at the bottom of the diagram and as a bullet list in the note below the embed. Example note text:

```markdown
- 🔵 OCI — 蓝色（Collector / Registry / Specs / Contracts / MySQL / Watchdog / Notifications）
- 🟡 AWS — 黄色（SQS / Step Functions / Lambda / DLQ）
- 🟢 GCP — 绿色（BigQuery raw/stage）
- 🟠 Cloudflare — 橙色（Workers Query API）
- 实线箭头 = 数据流 / 调度流
- 虚线箭头 = 读取引用（控制面对象）
```

## Replacing Existing Diagrams

When replacing a Mermaid or ASCII diagram already in a note:
- Do NOT delete the original section — add the new diagram as an appendix at the end of the document
- Add a blockquote noting the replacement: `> 以下为基于本文档内容重新绘制的系统架构图，原图见 §X.Y Mermaid 版本。`
- The user can compare and decide whether to remove the original

