# Account Locale Ownership Decision (OAC)

Status: Approved product/identity boundary decision, 2026-08-13.
Purpose: Resolves the account-locale convergence blockers raised in the group3
ui-sdk amendment review (issue e523c694, review report 9e9992e7bacf):
account-locale-integration-is-not-in-acceptance-closure,
authenticated-account-locale-owner-chain-absent,
ui-foundation-contract-cannot-bootstrap-its-declared-package-gate,
running-dag-amendment-recovery-set-is-incomplete.

This is the explicit contract-boundary decision required by the reviewer's
required_fix entries. The amendment built from this decision must treat it as
the authoritative boundary.

## 1. Ownership decision

The persisted user language preference (locale) is an **account-scoped user
display preference owned by the Identity domain**. It is modeled as an
`AccountPreference` configuration attached to the authenticated Principal,
using the approved mutable-CAS-configuration change mechanism from AGENTS.md.

Not owned by:

- UI / Console: consumption only. The frontend is never the persistence
  authority and holds no state machine for the preference.
- Project, Scope, Solution, Workflow: locale is a user display preference,
  not a business attribute (per docs/design/frontend/DESIGN.md,
  Localization and Language Switching section).
- Public site / documentation: pre-login surfaces stay browser-local and
  create no server-side records (per docs/design/detailed/
  10-web-api-cli-detailed-design.md, public-site section).

## 2. Value domain and resolution

- First release value domain: `locale ∈ {zh-CN, en-US}`. A missing persisted
  value is `unknown`, never silently defaulted in storage.
- Display resolution order is already fixed by the frontend authority
  (docs/design/frontend/DESIGN.md, Locale resolution):
  1. the authenticated user's explicitly saved preference;
  2. the unauthenticated user's local (browser) preference;
  3. browser preferred language;
  4. `en-US` fallback.
- Explicit choice always wins over auto-detection. Organizations and Projects
  must never silently override a personal locale.

## 3. Persistence, API, and authorization

- Persisted in PostgreSQL as an account-scoped configuration record owned by
  the Identity domain. No second write path, no shadow store.
- Mutated through exactly one CAS-guarded command and read through one query,
  both exposed by the existing `api-identity-authentication` adapter. No new
  service, package type, or extension surface is introduced.
- Authorization: the authenticated subject may read and update only its own
  preference; every protected operation is authorized; changes flow through
  the canonical audit path where governance rules classify them as
  security-sensitive.
- Login migration: the unauthenticated browser-local preference may be
  migrated to `AccountPreference` through one idempotent command at login.

## 4. UI consumption

- The Console reads the preference at login and applies locale resolution.
- The language switch control remains where the frontend authority already
  places it (docs/design/frontend/DESIGN.md line ~586): the authenticated
  account menu plus Command Palette action. No new `/settings/*` route is
  introduced; the existing `/settings/skills|model-providers|mcp|credentials`
  pages remain platform-configuration surfaces owned by their domain nodes.
- Switching updates in place without full-page navigation and never changes
  resource identity, URLs, query conditions, or Workflow state.

## 5. DAG shape required by this decision

- Add exactly one compensating authoring node, for example
  `account-locale-preference`, owned by the Identity domain:
  - blocked_by at least: `contracts-identity` (done), `authentication-sdk`
    (done);
  - contract scope: typed `AccountPreference` (locale), CAS update command,
    query projection, api-identity-authentication exposure, login migration
    idempotency, and participation in the UJ-IDENTITY-001 acceptance closure;
  - standard verification: owned-scope tests at >=90% statement coverage plus
    the release packaging/ownership gate used by comparable contract nodes.
- Update `ui-foundation`: remove locale persistence responsibility from its
  contract; declare consumption of the AccountPreference contract through a
  typed input binding (digest-bound upstream component manifest, same pattern
  as the group2 amendment used for generated inputs), so its declared package
  gate bootstraps from owned scope alone. Keep the account-menu switch UX
  responsibility defined by the frontend authority.
- Refresh the responsibility matrix so the UJ-IDENTITY-001 full owner chain
  includes the account-locale integration, and revalidate the amended graph.
- Do not change completed-node facts. Do not introduce tenant/organization
  locale policy, per-Project locale, user-content translation, or a generic
  preference framework beyond the locale field in the first release.
