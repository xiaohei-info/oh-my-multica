# Port Notes — baoyu-infographic

Ported from [JimLiu/baoyu-skills](https://github.com/JimLiu/baoyu-skills) v1.56.1.

## Changes from upstream

Only `SKILL.md` was modified. All 45 reference files are verbatim copies.

### SKILL.md adaptations

| Change | Upstream | Generic agent port |
|--------|----------|--------|
| Metadata namespace | `openclaw` | Runtime-specific metadata retained for compatibility |
| Trigger | `/baoyu-infographic` slash command | Natural language skill matching |
| User config | EXTEND.md file (project/user/XDG paths) | Removed from the portable package |
| User prompts | `AskUserQuestion` (batched) | Use the interactive prompt capability available in the runtime |
| Image generation | baoyu-imagine (Bun/TypeScript) | Use the image-generation capability available in the runtime |
| Platform support | Linux/macOS/Windows/WSL/PowerShell | Linux/macOS only |
| File operations | Bash commands | Use the file-reading and editing capabilities available in the runtime |

### What was preserved

- All layout definitions (21 files)
- All style definitions (21 files)
- Core reference files (analysis-framework, base-prompt, structured-content-template)
- Recommended combinations table
- Keyword shortcuts table
- Core principles and workflow structure
- Author, version, homepage attribution

## Syncing with upstream

To pull upstream updates:
```bash
# Compare versions
curl -sL https://raw.githubusercontent.com/JimLiu/baoyu-skills/main/skills/baoyu-infographic/SKILL.md | head -5
# Look for version: line

# Diff reference files
diff <(curl -sL https://raw.githubusercontent.com/.../references/layouts/bento-grid.md) references/layouts/bento-grid.md
```

Reference files can be overwritten directly when unchanged from upstream. Merge `SKILL.md` manually because it contains runtime-specific adaptations.
