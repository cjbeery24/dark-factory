#!/usr/bin/env bash
# Sets up one experiment phase.
#
# Runs once before the first trial of a phase:
#   1. Checks out the phase's dark-factory branch
#   2. Records a fingerprint (dark-factory SHA, claude CLI version)
#   3. Backs up target's config.toml (see note below)
#   4. Runs dark-factory init --update to sync agents/templates
#   5. Restores config.toml (init --update overwrites it non-interactively)
#   6. Rebuilds Docker images against the current dark-factory code
#   7. Records image digests for reproducibility
#
# Note on config.toml: darkfactory/setup/init.py:125-134 only PROMPTS to
# preserve config.toml when stdin is a tty. Shell-scripted invocation
# (non-tty) skips the prompt and calls _write_config(), which regenerates
# config.toml from detection. To keep the target's tuned config stable
# across phases, we back up and restore it explicitly.
set -euo pipefail

: "${DF:?DF env var must be set to dark-factory repo path}"
: "${TARGET:?TARGET env var must be set to target project path}"
: "${PHASE:?PHASE env var must be set (e.g. phase-0-baseline)}"
: "${BRANCH:?BRANCH env var must be set (e.g. experiment/phase-0-baseline)}"
: "${LOGDIR:?LOGDIR env var must be set}"
: "${LANGUAGE:?LANGUAGE env var must be set (e.g. node, python, go, rust)}"

mkdir -p "$LOGDIR"

# 1. Checkout phase branch. Fail loudly on uncommitted dark-factory changes.
(cd "$DF" && git diff --quiet && git diff --cached --quiet) \
  || { echo "ERROR: $DF has uncommitted changes"; exit 1; }
(cd "$DF" && git checkout "$BRANCH")

# 2. Fingerprint the phase
(cd "$DF" && git rev-parse HEAD) > "$LOGDIR/df-sha.txt"
(cd "$DF" && git log -1 --format='%s' HEAD) >> "$LOGDIR/df-sha.txt"
claude --version > "$LOGDIR/claude-cli-version.txt" 2>&1 || true

# 3. Back up config.toml (see script-header note)
CONFIG_BACKUP="${TARGET}/.dark-factory/config.toml.experiment-bak"
if [ -f "${TARGET}/.dark-factory/config.toml" ]; then
  cp "${TARGET}/.dark-factory/config.toml" "$CONFIG_BACKUP"
fi

# 4. Sync agents + templates from the installed darkfactory package
(cd "$TARGET" && dark-factory init --update --non-interactive)

# 5. Restore the pre-existing config.toml if there was one
if [ -f "$CONFIG_BACKUP" ]; then
  mv "$CONFIG_BACKUP" "${TARGET}/.dark-factory/config.toml"
fi

# 6. Rebuild Docker images with current dark-factory code
DF_PKG_PATH=$(python3 -c "import darkfactory, pathlib; print(pathlib.Path(darkfactory.__file__).parent.parent)")
bash "${DF_PKG_PATH}/docker/build-images.sh" "$LANGUAGE"

# 7. Record image digests for reproducibility
docker image ls --digests | grep -E '^dark-factory-' > "$LOGDIR/docker-images.txt"

echo "=== Phase ${PHASE} setup complete ==="
echo "  df SHA:  $(head -1 "$LOGDIR/df-sha.txt")"
echo "  Images:  $(wc -l < "$LOGDIR/docker-images.txt") dark-factory images recorded"
echo "  LogDir:  $LOGDIR"
