# Plan: Project-configurable egress allowlist

## Motivation

The execution proxy filter (`filter-execution.txt`) is baked into the `dark-factory-proxy`
Docker image. It contains the standard package registries for all supported languages
(npm, PyPI, Go module proxy, crates.io). This covers the common case, but some npm packages
download prebuilt binaries from GitHub releases during `postinstall` (e.g., `turbo`, some
native addons). Those downloads would 403 because `objects.githubusercontent.com` is not
in the filter.

The wrong fix is adding `github.com` globally — that's a massive exfiltration surface that
undermines the point of egress filtering. The right fix is letting projects declare their
own additional egress needs explicitly.

Currently, adding an extra host requires editing `filter-execution.txt` (a framework file)
and rebuilding the proxy Docker image. This is the wrong ownership model: a project's
network requirements are a project concern, not a framework concern.

## Design

### New config field

Add `egress_extra_hosts` to `PipelineConfig`:

```toml
# config.toml
[gates]
egress_extra_hosts = ["objects.githubusercontent.com"]
```

Each entry is a tinyproxy `FilterExtended` regex pattern applied to the `host:port` CONNECT
target string. The standard pattern form is `^hostname(:\d+)?$`. Document this clearly —
users need to know entries are regexes, not plain hostnames.

Default: empty list (no change to current behavior).

### Runtime filter generation

At proxy startup, `setup_proxies()` in `darkfactory/docker/networks.py`:

1. Reads the baked-in `filter-execution.txt` content from the proxy image (or from the
   dark-factory package's own copy of the file)
2. Appends any `egress_extra_hosts` entries from config, formatted as
   `^{host}(:\d+)?$` if the entry doesn't already look like a regex (i.e., doesn't start
   with `^`)
3. Writes the merged content to a temp file: `/tmp/df-{run_id}-filter-execution.txt`
4. Bind-mounts it over the baked-in file in the proxy container:
   `--mount type=bind,source=/tmp/df-{run_id}-filter-execution.txt,target=/etc/tinyproxy/filter-execution.txt,readonly`

The temp file is written fresh each pipeline run and cleaned up with the rest of the run
artifacts. No proxy image rebuild needed for project-specific entries.

The analysis filter is never extended. Analysis containers have no legitimate reason to
reach any package registry. `egress_extra_hosts` applies to the execution proxy only.

### Filter file sourcing

Two approaches for getting the base filter content at runtime; choose one:

**A) Read from the installed package (simpler)**
The `filter-execution.txt` file lives in `darkfactory/docker/filter-execution.txt` and is
included in the package via `MANIFEST.in` / `package_data`. `setup_proxies()` reads it
using `importlib.resources` or a relative path from the package root.

**B) Read from the proxy image (more complex, not recommended)**
Run `docker run --rm dark-factory-proxy:latest cat /etc/tinyproxy/filter-execution.txt`
to extract the baked-in content. This couples the host-side logic to the image and adds
a Docker exec per pipeline run. Not worth it.

Go with **A**.

### Cleanup

Add cleanup of the temp filter file to `teardown_proxies()` or wherever run artifacts are
cleaned up. Use a `try/finally` or best-effort `os.unlink` — a leftover temp file is not
a security issue (it has no effect after the proxy container is gone) but is noise.

---

## Files to change

| File | Change |
|------|--------|
| `darkfactory/models/config.py` | Add `egress_extra_hosts: list[str] = field(default_factory=list)` |
| `darkfactory/config/defaults.py` | Add `"egress_extra_hosts"` to `GATES_FIELDS` mapping |
| `darkfactory/docker/networks.py` | Generate merged filter file and bind-mount it for execution proxy |
| `docs/CONFIGURATION.md` | Document `egress_extra_hosts`, explain regex format, show GitHub example |

No changes to Dockerfiles or the proxy image are required.

---

## Security properties

**Default behavior**: unchanged. Projects with no `egress_extra_hosts` get the same
execution filter as today.

**With extra hosts**: the user has explicitly declared additional outbound targets in their
project's `config.toml`, which lives in version control. The allowlist expansion is
auditable, reviewable in PRs, and scoped to that project's pipeline runs.

**Injection risk**: tinyproxy `FilterExtended` entries are regexes evaluated by tinyproxy,
not by the shell or dark-factory. A malformed pattern can at worst allow more traffic than
intended — it cannot cause code execution. Users should be warned in docs that entries are
regexes (e.g., `.` matches any character; use `\.` for a literal dot).

**Analysis proxy**: never extended. The `egress_extra_hosts` field has no effect on the
analysis proxy or any other container type.

---

## What NOT to do

- Do not add `github.com` or `objects.githubusercontent.com` to the baked-in filter.
  These are exfiltration surfaces. They belong in project config when explicitly needed,
  never in framework defaults.
- Do not extend the analysis filter. Scenario Architect, Spec Validator, and Reviewer
  containers have no package-install step and no legitimate outbound registry need.
- Do not pass extra hosts as a CLI flag to the pipeline — config.toml is the right place
  since this is a per-project persistent setting, not a per-run override.

---

## Open questions

- Should dark-factory validate entries against a known-safe pattern (e.g., must be a
  hostname-like regex, reject obvious wildcards like `.*`)? A light lint step could prevent
  accidental `.*` entries that effectively disable filtering. Low priority — the user is
  already in config.toml with full knowledge of what they're doing.
- Should `dark-factory init` detect known binary-downloading packages in `package.json`
  (e.g., `turbo`, `@swc/core`) and auto-suggest the needed `egress_extra_hosts` entries?
  Nice-to-have, not blocking.
