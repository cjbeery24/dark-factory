#!/usr/bin/env bash
# scripts/lib/tui.sh — Interactive spec selector (TUI)
#
# Provides: select_spec
# Requires global: CI_MODE
# Sets global: SPEC_NAME

[[ -n "${_DF_LIB_TUI_LOADED:-}" ]] && return 0
_DF_LIB_TUI_LOADED=1

select_spec() {
    local specs_dir=".dark-factory/specs"
    local -a names=()
    local -a dates=()
    local file base

    # Collect specs newest-first
    while IFS= read -r file; do
        base="$(basename "$file" .md)"
        names+=("$base")
        if [[ "$(uname)" == "Darwin" ]]; then
            dates+=("$(stat -f '%Sm' -t '%Y-%m-%d' "$file")")
        else
            dates+=("$(date -r "$file" '+%Y-%m-%d')")
        fi
    done < <(ls -t "$specs_dir"/*.md 2>/dev/null)

    local count=${#names[@]}

    if (( count == 0 )); then
        echo "ERROR: No specs found in $specs_dir/"
        echo "Create a spec first using a template from templates/"
        exit 1
    fi

    if (( count == 1 )); then
        echo "Auto-selecting only spec: ${names[0]}"
        SPEC_NAME="${names[0]}"
        return
    fi

    # CI mode: cannot interactively select
    if [[ "$CI_MODE" == "true" ]]; then
        echo "ERROR: Multiple specs found and --ci mode cannot interactively select."
        echo "Provide spec name as first argument."
        exit 1
    fi

    local sel=0  # 0-indexed

    _df_cleanup() {
        printf '\033[?25h'     # show cursor
        printf '\033[?1049l'   # exit alternate screen
    }
    trap '_df_cleanup; exit 130' INT

    printf '\033[?1049h'       # enter alternate screen
    printf '\033[?25l'         # hide cursor

    _df_draw() {
        printf '\033[H\033[J'  # move to top-left + clear screen
        local i line maxw=$(( ${COLUMNS:-80} - 10 ))
        echo "Dark Factory — select a spec:"
        echo ""
        for (( i=0; i<count; i++ )); do
            line="${names[$i]}  (${dates[$i]})"
            if (( ${#line} > maxw )); then
                line="${line:0:$((maxw-1))}…"
            fi
            if (( i == sel )); then
                printf '  \033[36m>\033[0m \033[7m %d) %s \033[0m\n' $((i+1)) "$line"
            else
                printf '    %d) %s\n' $((i+1)) "$line"
            fi
        done
        printf '\n  \033[2m↑/↓ navigate • 1-99 jump • enter select • q quit\033[0m'
    }

    _df_select() {
        _df_cleanup
        trap - INT
        SPEC_NAME="${names[$sel]}"
    }

    _df_draw

    local key key2 key3
    while true; do
        read -rsn1 key
        case "$key" in
            $'\x1b')
                key2='' key3=''
                read -rsn1 -t 0.1 key2
                read -rsn1 -t 0.1 key3
                case "${key2}${key3}" in
                    '[A'|'OA') (( sel > 0 )) && (( sel-- )) || true ;;
                    '[B'|'OB') (( sel < count - 1 )) && (( sel++ )) || true ;;
                esac
                ;;
            [0-9])
                # Support two-digit entry: wait briefly for a second digit (L14)
                local num="$key" next=""
                read -rsn1 -t 0.3 next 2>/dev/null || true
                [[ "$next" =~ ^[0-9]$ ]] && num="${num}${next}"
                local target=$(( num - 1 ))
                if (( target >= 0 && target < count )); then
                    sel=$target
                    _df_select
                    return
                fi
                ;;
            ''|$'\n')  # Enter
                _df_select
                return
                ;;
            q|Q)
                _df_cleanup
                trap - INT
                exit 0
                ;;
        esac
        _df_draw
    done
}
