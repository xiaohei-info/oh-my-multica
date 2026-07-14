# User-visible localization design

## Status

Approved for implementation on 2026-07-14.

## Goal

Make every OMAC-maintained, user-visible surface available in English and
Simplified Chinese. English is the primary language. Chinese is a complete
mirror, not an inline translation or a runtime machine translation.

The project language is chosen during interactive `omac init` and stored in
`.omac/config.yaml`:

```yaml
language: en # or cn
```

If the key or the config file is missing, OMAC uses English. No command adds a
`--lang` or `--language` flag, and no environment variable overrides the saved
choice.

## Scope

Included:

- CLI help, interactive prompts, successful output, errors, hints, and logs.
- User-facing Web text and API-generated errors.
- `omac guide` indexes and every guide topic.
- OMAC-authored agent template instructions and other text that OMAC presents
  to a human or dispatched agent.
- README, changelog, and all files under `docs/`.

Excluded:

- Python comments, docstrings, identifiers, and test names or descriptions.
- JSON keys, exit codes, command names, flags, enum values, file paths, URLs,
  and code blocks.
- Text supplied by a user, the platform, an issue, a contract, a PR, or an
  evidence file. OMAC must report those facts without translating them.
- Vendored third-party skill material that OMAC does not render as one of its
  own entry points.

## Data model and language resolution

Add one validated top-level config key: `language` with values `en` and `cn`.
The resolver returns `en` when the key is absent. Invalid values raise a
validation error that explains the accepted values and how to repair the
configuration with `omac config set language <en|cn>`.

Interactive `omac init` asks for the language before any other prompt. It uses
English for that first prompt when no saved choice exists. Once the user chooses
`cn`, the rest of that same wizard uses Chinese. Non-interactive `omac init`
writes `language: en`; the existing `omac config set language cn` path changes
an initialized project later.

The language is process-local configuration, read before a command renders its
first user-facing message. It is never stored in manifests, work items, or
platform metadata.

## Runtime messages

Create a small internal localization module with:

- one resolver for the current configured language;
- an English and Chinese catalog keyed by stable message IDs;
- a `t(key, **values)` helper for interpolation.

User-visible Python code calls `t()` rather than embedding an English or
Chinese sentence. The catalogs own prose; code owns data and control flow.
This prevents language conditionals from spreading across commands, engines,
pipelines, and Web handlers.

For example, `work show` keeps its exact JSON schema and its executable
`submit` command. Its OMAC-authored prose, such as `protocol` and `authority`,
is localized. Its `task.title`, `context.issue_description`, contract content,
and other externally supplied values remain unchanged.

The Web layer continues to parse request parameters, call command functions,
and return their JSON unchanged. It may select language only through the same
project configuration; it must not add a separate Web-only language state or
translate payloads after command execution.

## Static documents and guides

Static prose uses complete paired Markdown files rather than message catalogs.

- `README.md` is the English primary document.
- `README.zh-CN.md` is its Chinese mirror.
- `docs/<path>.md` is the English primary document.
- `docs/zh-CN/<path>.md` is the Chinese mirror at the same relative path.
- Guides have parallel `en/` and `zh-CN/` trees under `src/omac/guide/`.
  `omac guide` loads the tree selected by `language`.

Every guide topic has one full English document and one full Chinese document.
They must preserve the same rules, heading hierarchy, fenced command examples,
anchors, and referenced `omac` commands. The prose may be idiomatic in each
language; it must not omit requirements or change behavior.

OMAC-authored agent template instructions follow the same pairing convention
and are selected before provisioning. Command examples and externally supplied
skill files remain unchanged.

## Documentation style

The English README is rewritten for first use:

1. one-sentence product definition and concrete outcome;
2. what changes for a team and why the deterministic loop matters;
3. prerequisites and installation;
4. a short, verified first-run path;
5. command reference, exit-code contract, and links to deeper material.

It should explain OMAC plainly, avoid inflated claims, and keep agent and human
entry points distinct. The Chinese mirror has the same facts, commands, and
structure while reading naturally in Chinese.

## Compatibility

- CLI paths, flags, exit codes, JSON keys, enum values, and command examples do
  not change.
- Missing `language` now resolves to English by design. This is the only
  intentional presentation change for existing configurations and is recorded
  in the changelog.
- A project can switch future output by changing `language`; no stored task or
  platform record is rewritten.
- Web responses remain byte-for-byte the command JSON for the project language.

## Verification

Tests cover:

1. missing language defaults to English; `cn` selects Chinese; invalid values
   fail with the stable validation exit code;
2. interactive `init` writes the selected value and changes its remaining
   prompts; non-interactive initialization writes English;
3. every supported command's user-facing success, error, and help paths use the
   configured language;
4. `work show` changes only OMAC-authored prose across languages and keeps
   schema, user/platform facts, and `submit` commands identical;
5. Web endpoint output remains identical to the corresponding command JSON;
6. each English document and guide has a Chinese mirror with matching headings,
   code fences, and `omac` command references;
7. every changed documentation command is exercised or linked to an existing
   end-to-end test.

Completion requires `python3 -m pytest tests/` to pass.
