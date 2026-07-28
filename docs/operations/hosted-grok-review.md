# Hosted Grok PR review

The workflow in `.github/workflows/grok-pr-review.yml` runs a semantic review
for same-repository, non-draft pull requests. It checks out the pull request
head as the review target, then fetches review scripts and prompts separately
from the public `xliberty2008x/grok-plugin` repository. The trusted runtime is
isolated under `RUNNER_TEMP`; no review script is executed from the pull
request.

## Privacy and trust boundary

Enabling this workflow sends the pull request code and diff to xAI. Do not use
it for changes whose contents must remain entirely local or for data that the
repository's xAI usage policy does not permit.

The workflow assumes that maintainers of the trusted
`xliberty2008x/grok-plugin` runtime are trusted collaborators. Its default ref
is the immutable commit
`263906944acb6e131ff38d14280639ae6ef5b567`. Review any replacement before
changing that pin. The checked-out pull request tree is review input only, and
the workflow executes no script from it.

Repository secrets are available to workflows changed by same-repository pull
requests. Therefore, every person allowed to push a branch and open a pull
request in this repository must be trusted with the Grok credential. The
same-repository gate is not a security boundary against a malicious
collaborator who edits this workflow. If that assumption does not hold, protect
the secret with a GitHub Environment that requires maintainer approval or
disable this workflow.

Fork pull requests are skipped because repository secrets are not exposed to
them. Draft pull requests are skipped until `ready_for_review`. The workflow
uses `pull_request`, never `pull_request_target`.

The review child receives Grok authentication but no `GH_TOKEN` or
`GITHUB_TOKEN`. A separate trusted posting step receives the GitHub token but
not Grok authentication. Staged `auth.json` files are removed in a final
`always()` cleanup step.

Large pull requests may exceed the Grok provider context limit before semantic
review. Split very large changes into smaller pull requests; a context-limit
failure is an infrastructure/input-size failure, not a code finding.

## Repository configuration

Create one encrypted GitHub Actions repository secret with this exact name:

- `GROK_AUTH_JSON` — the complete, current contents of Grok Build
  `~/.grok/auth.json`

Never paste that value into a workflow, repository file, command argument, log,
or Git metadata.

The workflow also accepts these optional GitHub Actions repository variables:

- `GROK_PR_REVIEW_TRUSTED_REF` — reviewed commit or ref in
  `xliberty2008x/grok-plugin`; defaults to
  `263906944acb6e131ff38d14280639ae6ef5b567`
- `GROK_CLI_VERSION` — `@xai-official/grok` version; defaults to `0.2.112`

Prefer an immutable commit for `GROK_PR_REVIEW_TRUSTED_REF`. A branch ref can
change without a workflow change and expands the trusted-collaborator boundary.

## Keep the Actions secret synchronized

Use the existing repository-parameterized watcher from a trusted local checkout
of `xliberty2008x/grok-plugin`; do not copy its auth-sync scripts into this
repository. The checkout must contain
`scripts/install-grok-ci-auth-sync.mjs` and `scripts/sync-grok-ci-auth.mjs`.

Set absolute paths for the trusted checkout, Node.js, and GitHub CLI, then
confirm that the two executable paths begin with `/`:

```bash
GROK_PLUGIN_ROOT="/absolute/path/to/trusted/grok-plugin"
NODE_BIN="$(command -v node)"
GH_BIN="$(command -v gh)"
```

Authenticate both tools before installation:

```bash
grok login
gh auth login
```

Install the macOS LaunchAgent for this repository. It watches
`~/.grok/auth.json`, retries every five minutes, and performs an immediate
forced synchronization:

```bash
"$NODE_BIN" \
  "$GROK_PLUGIN_ROOT/scripts/install-grok-ci-auth-sync.mjs" install \
  --repo xliberty2008x/digital-brain-avatar \
  --node-bin "$NODE_BIN" \
  --gh-bin "$GH_BIN"
```

After a later `grok login`, the changed auth file triggers an upload to the
encrypted Actions secret. The watcher stores only private installation metadata
and a digest of the last successful upload, never the credential itself.

Inspect watcher and synchronization health:

```bash
"$NODE_BIN" \
  "$GROK_PLUGIN_ROOT/scripts/install-grok-ci-auth-sync.mjs" status \
  --repo xliberty2008x/digital-brain-avatar
```

Force a manual synchronization when needed. Use the absolute state directory
printed by the installer or status command; do not derive its repository digest
by hand:

```bash
STATE_DIR="/absolute/path/printed/by/status"
"$NODE_BIN" \
  "$GROK_PLUGIN_ROOT/scripts/sync-grok-ci-auth.mjs" \
  --repo xliberty2008x/digital-brain-avatar \
  --gh-bin "$GH_BIN" \
  --state-dir "$STATE_DIR" \
  --force
```

Remove the watcher without deleting GitHub's stored secret:

```bash
"$NODE_BIN" \
  "$GROK_PLUGIN_ROOT/scripts/install-grok-ci-auth-sync.mjs" uninstall \
  --repo xliberty2008x/digital-brain-avatar
```

Status reports installation health as `absent`, `degraded`, or `loaded`, and
sync state as `pending` or `current`. An expired or insufficiently fresh local
session remains pending; run `grok login` again and let the watcher retry.
