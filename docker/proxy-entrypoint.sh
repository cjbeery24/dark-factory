#!/bin/sh
# dark-factory egress proxy entrypoint.
#
# Optionally widens the tinyproxy allowlist with operator-configured hosts
# (local-LLM routing, Phase 4) BEFORE starting the proxy, then execs tinyproxy.
#
# DF_EXTRA_ALLOW_REGEX: newline-joined extended-regex lines (e.g.
#   "^host\.docker\.internal:11434$") to append to the baked filter allowlist.
#   The lines are generated + validated by darkfactory.docker.local_llm
#   (anchored, re.escaped, port-pinned, control-chars rejected).
#
# Guards (all Linus-reviewed):
#  - EMPTY: a blank line appended to a FilterDefaultDeny extended-regex file is an
#    empty pattern that can match-all = catastrophic allowlist failure. Append ONLY
#    when the var is non-empty AND skip blank lines. Unset => byte-identical to the
#    pre-Phase-4 image.
#  - IDEMPOTENT (BLOCKING-1): this is an ENTRYPOINT — it runs on every `docker
#    start`, and the filter files persist in the writable layer. Appending blindly
#    would RE-APPEND on every restart (unbounded growth). `grep -qxF` each line so a
#    restart reproduces identical bytes.
#  - LOUD FAIL-CLOSED (BLOCKING-2): a failed append (read-only FS, etc.) must NOT
#    silently take down all egress. Fail closed (a missing allowlist entry denies the
#    host) but emit a FATAL naming the file so the operator has a breadcrumb instead
#    of a mystery connection error. The while-loop reads from a FILE redirect (not a
#    pipe) so it runs in THIS shell and `exit 1` actually aborts the script.
#
# We append to BOTH baked filter files; a proxy container only loads the one its
# TINYPROXY_CONF selects, so touching the other is inert. Per-net_type SCOPING
# happens upstream (setup_proxies passes DF_EXTRA_ALLOW_REGEX only to the proxies
# whose net_type has a local backend). printf '%s\n' (not echo) preserves the regex
# backslashes/$ verbatim.
set -e

FILTER_DIR="${DF_FILTER_DIR:-/etc/tinyproxy}"

if [ -n "${DF_EXTRA_ALLOW_REGEX:-}" ]; then
    # Explicit XXXXXX template — portable across busybox (Alpine), BSD, and GNU mktemp.
    _df_regex_tmp="$(mktemp "${TMPDIR:-/tmp}/df-proxy-allow.XXXXXX")"
    printf '%s\n' "$DF_EXTRA_ALLOW_REGEX" > "$_df_regex_tmp"
    for f in "$FILTER_DIR/filter-analysis.txt" "$FILTER_DIR/filter-execution.txt"; do
        while IFS= read -r _df_line; do
            [ -n "$_df_line" ] || continue                 # skip blank lines
            grep -qxF -- "$_df_line" "$f" 2>/dev/null && continue   # idempotent
            if ! printf '%s\n' "$_df_line" >> "$f"; then
                echo "proxy-entrypoint: FATAL: cannot write egress allowlist line to $f" >&2
                exit 1
            fi
        done < "$_df_regex_tmp"
    done
    rm -f "$_df_regex_tmp"
fi

# DF_EXTRA_CONNECT_PORT: newline-joined ports (e.g. "3456") to permit for the HTTP
# CONNECT method. claude -p tunnels to a local backend via CONNECT even for an
# http:// base_url, and tinyproxy's ConnectPort default is 443-only → a host-bridge
# backend on a non-443 port is "Refused CONNECT method on port N" without this.
# Appends `ConnectPort N` to ONLY the ACTIVE conf ($TINYPROXY_CONF — the one this
# container loads), unlike the allowlist block which writes BOTH filter files (the
# active conf is the only one that matters, so there's no point touching the other).
# Shares the allowlist block's GUARDS: digits-only (no arbitrary-line injection),
# idempotent (grep -qxF), fail-closed + loud (file-redirect so `exit 1` aborts in
# THIS shell, not a pipe subshell).
if [ -n "${DF_EXTRA_CONNECT_PORT:-}" ]; then
    _conf="${TINYPROXY_CONF:-/etc/tinyproxy/tinyproxy-analysis.conf}"
    _df_port_tmp="$(mktemp "${TMPDIR:-/tmp}/df-proxy-port.XXXXXX")"
    printf '%s\n' "$DF_EXTRA_CONNECT_PORT" > "$_df_port_tmp"
    while IFS= read -r _df_port; do
        [ -n "$_df_port" ] || continue
        case "$_df_port" in *[!0-9]*) continue ;; esac        # digits only (defense)
        _df_cp_line="ConnectPort $_df_port"
        grep -qxF -- "$_df_cp_line" "$_conf" 2>/dev/null && continue   # idempotent
        if ! printf '%s\n' "$_df_cp_line" >> "$_conf"; then
            echo "proxy-entrypoint: FATAL: cannot write ConnectPort to $_conf" >&2
            exit 1
        fi
    done < "$_df_port_tmp"
    rm -f "$_df_port_tmp"
fi

exec tinyproxy -d -c "${TINYPROXY_CONF:-/etc/tinyproxy/tinyproxy-analysis.conf}"
