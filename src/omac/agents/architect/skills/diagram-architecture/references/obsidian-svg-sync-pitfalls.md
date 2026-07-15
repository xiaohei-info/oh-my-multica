# Obsidian / SVG sync pitfalls

Use this when architecture diagrams are being maintained as SVG assets plus Markdown/HTML wrappers.

## Durable lessons

1. Keep **one canonical SVG asset**.
   - Markdown/Obsidian should usually embed the SVG asset by path.
   - HTML wrappers should reference that same SVG asset.
   - Avoid maintaining three divergent copies: inline SVG in `.md`, standalone `.svg`, and separate inline SVG in `.html`.

2. Never write tool-decorated file output back into the real asset.
   - Some file-reading tools render lines as `N|content`.
   - If that output is pasted back into `.md`, `.svg`, or `.html`, the artifact is corrupted with visible line-number prefixes.
   - Safe recovery path: return to the clean source asset, strip prefixes, then re-sync wrappers.

3. SVG text does **not** auto-wrap.
   - Long labels/descriptions must be manually split with `<tspan>`.
   - If the text is near the box width, wrap first; only then consider smaller font or larger box.
   - Watch for both horizontal overflow and bottom clipping.

4. Validate with a rendered view, not only source inspection.
   - Open the HTML/SVG in a browser or generate a screenshot.
   - Look specifically for: text outside boxes, clipped descenders/bottom lines, overlapping labels, and stray line-number prefixes.

## Recovery checklist

- Rebuild clean `.md` references to point at the SVG asset.
- Rebuild `.html` wrapper to reference the SVG asset.
- Re-open or screenshot the rendered diagram.
- Fix any long labels with `tspan` line breaks.
- Re-check the heaviest text zones before handoff.

