#!/usr/bin/env bash
# scripts/lib/satisfaction.sh — Satisfaction scoring, multi-run, trajectory analysis
#
# Provides:
#   _df_layer_weight              — map scenario filename to configurable weight
#   _df_multi_run_file            — run a test command N times, pass if >=K succeed
#   _df_build_file_cmd            — build per-file test runner command
#   _df_run_scenarios_satisfaction — multi-run + weighted scoring for scenario files
#   _df_parse_per_layer_output    — extract per-layer pass/fail from test runner output
#   _df_classify_trajectory       — classify satisfaction score trajectory
#   _df_layer_trajectory          — per-category pass rates across iterations
#   _df_strategy_recommendation   — actionable recommendation from trajectory
#   _df_build_oracle_analysis     — full iteration intelligence block for Oracle
#   _df_validate_satisfaction_config — validate Phase 3 config variables

[[ -n "${_DF_LIB_SATISFACTION_LOADED:-}" ]] && return 0
_DF_LIB_SATISFACTION_LOADED=1

# ==============================================================================
# Layer weight mapping
# ==============================================================================

# _df_layer_weight <filename>
#   Maps scenario filename to weight using the NN- prefix convention.
#   Returns weight via stdout.
_df_layer_weight() {
    local filename
    filename="$(basename "$1")"
    local prefix="${filename%%-*}"

    case "$prefix" in
        01) echo "${SCENARIO_WEIGHT_CONTRACT:-100}" ;;
        02) echo "${SCENARIO_WEIGHT_HAPPY_PATH:-100}" ;;
        03) echo "${SCENARIO_WEIGHT_EDGE_CASES:-80}" ;;
        04) echo "${SCENARIO_WEIGHT_ERROR_CONDITIONS:-90}" ;;
        05) echo "${SCENARIO_WEIGHT_STATE_SEQUENCE:-80}" ;;
        06) echo "${SCENARIO_WEIGHT_SECURITY:-120}" ;;
        07) echo "${SCENARIO_WEIGHT_PROPERTY_BASED:-70}" ;;
        08) echo "${SCENARIO_WEIGHT_ADVERSARIAL:-60}" ;;
        *)  echo "100" ;;
    esac
}

# ==============================================================================
# Multi-run execution
# ==============================================================================

# Globals set by _df_multi_run_file
_MR_SUCCESSES=0
_MR_FAILURES=0

# _df_multi_run_file <test_cmd> <n> <k>
#   Runs test_cmd N times. Returns 0 if >=K succeed.
#   Early-exit: stop once K successes achieved or K is impossible.
_df_multi_run_file() {
    local test_cmd="$1"
    local n="$2"
    local k="$3"
    _MR_SUCCESSES=0
    _MR_FAILURES=0

    local remaining="$n"
    local i
    for (( i=1; i<=n; i++ )); do
        local rc=0
        bash -c "$test_cmd" >/dev/null 2>&1 || rc=$?
        remaining=$((remaining - 1))

        if [[ $rc -eq 0 ]]; then
            _MR_SUCCESSES=$((_MR_SUCCESSES + 1))
            # Early exit: K successes achieved
            if [[ $_MR_SUCCESSES -ge $k ]]; then
                _MR_FAILURES=$((i - _MR_SUCCESSES))
                return 0
            fi
        else
            _MR_FAILURES=$((_MR_FAILURES + 1))
            # Early exit: K is mathematically impossible
            local max_possible=$((_MR_SUCCESSES + remaining))
            if [[ $max_possible -lt $k ]]; then
                return 1
            fi
        fi
    done

    if [[ $_MR_SUCCESSES -ge $k ]]; then
        return 0
    else
        return 1
    fi
}

# ==============================================================================
# Per-file command builder
# ==============================================================================

# _df_build_file_cmd <filepath> <toolchain>
#   Builds a test runner command for a single scenario file.
#   Outputs the command via stdout. Empty output means skip per-file.
_df_build_file_cmd() {
    local filepath="$1"
    local toolchain="$2"

    if [[ -n "${SCENARIO_TEST_CMD:-}" ]]; then
        if [[ "$SCENARIO_TEST_CMD" == *"{FILE}"* ]]; then
            echo "${SCENARIO_TEST_CMD//\{FILE\}/$filepath}"
            return
        fi
        # Has {SCENARIOS_DIR} but not {FILE} — can't do per-file
        echo ""
        return
    fi

    case "$toolchain" in
        node)   echo "npx vitest run '${filepath}' 2>&1 || npx jest '${filepath}' 2>&1" ;;
        python) echo "pytest '${filepath}' -v 2>&1" ;;
        go|rust) echo "" ;;  # No reliable per-file default
        *)      echo "" ;;
    esac
}

# ==============================================================================
# Multi-run + weighted scoring
# ==============================================================================

# _df_run_scenarios_satisfaction <scenarios_dir> <toolchain> <spec_name> <iteration>
#   Multi-run + weighted scoring. Called when SCENARIO_MULTI_RUN_N > 1.
#   Writes scenario-counts.env and category-breakdown.env.
_df_run_scenarios_satisfaction() {
    local scenarios_dir="$1"
    local toolchain="$2"
    local spec_name="$3"
    local iteration="$4"
    local multi_n="${SCENARIO_MULTI_RUN_N:-1}"
    local multi_k="${SCENARIO_MULTI_RUN_K:-1}"

    local counts_file=".dark-factory/reports/${spec_name}-scenario-counts.env"
    local cat_file=".dark-factory/reports/${spec_name}-category-breakdown.env"
    local cat_iter_file=".dark-factory/reports/${spec_name}-category-iter${iteration}.env"
    mkdir -p ".dark-factory/reports"

    # Collect scenario files
    local -a files=()
    while IFS= read -r f; do
        files+=("$f")
    done < <(find "$scenarios_dir" -type f \( \
        -name '*.test.*' -o -name '*.spec.*' -o \
        -name 'test_*.py' -o -name '*_test.py' -o \
        -name '*_test.go' -o -name '*.rs' \
    \) 2>/dev/null | sort)

    if [[ ${#files[@]} -eq 0 ]]; then
        cat > "$counts_file" <<EOF
SCENARIO_PASSED=0
SCENARIO_FAILED=0
SCENARIO_TOTAL=0
SCENARIO_RATE=0
SATISFACTION_SCORE=0
MULTI_RUN_N=${multi_n}
MULTI_RUN_K=${multi_k}
EOF
        return
    fi

    # Per-category accumulators: cat_XX_passed, cat_XX_total, cat_XX_weight
    local -A cat_passed=() cat_total=() cat_weight=()
    local total_passed=0 total_failed=0

    local can_per_file=true
    local _sample_cmd
    _sample_cmd=$(_df_build_file_cmd "${files[0]}" "$toolchain")
    if [[ -z "$_sample_cmd" ]]; then
        can_per_file=false
    fi

    if [[ "$can_per_file" == "true" ]]; then
        # Per-file multi-run
        for filepath in "${files[@]}"; do
            local fname
            fname="$(basename "$filepath")"
            local prefix="${fname%%-*}"
            # Normalize unknown prefixes
            if [[ ! "$prefix" =~ ^[0-9][0-9]$ ]]; then
                prefix="99"
            fi

            local weight
            weight=$(_df_layer_weight "$filepath")

            local file_cmd
            file_cmd=$(_df_build_file_cmd "$filepath" "$toolchain")

            echo "  Multi-run (${multi_n}x, k=${multi_k}): ${fname}..."

            local mr_rc=0
            _df_multi_run_file "$file_cmd" "$multi_n" "$multi_k" || mr_rc=$?

            cat_total["$prefix"]=$(( ${cat_total["$prefix"]:-0} + 1 ))
            cat_weight["$prefix"]="$weight"

            if [[ $mr_rc -eq 0 ]]; then
                cat_passed["$prefix"]=$(( ${cat_passed["$prefix"]:-0} + 1 ))
                total_passed=$((total_passed + 1))
                echo "    PASS (${_MR_SUCCESSES}/${multi_n} successes)"
            else
                total_failed=$((total_failed + 1))
                echo "    FAIL (${_MR_SUCCESSES}/${multi_n} successes, needed ${multi_k})"
            fi
        done
    else
        # Directory-level multi-run (coarser)
        local dir_cmd=""
        if [[ -n "${SCENARIO_TEST_CMD:-}" ]]; then
            dir_cmd="${SCENARIO_TEST_CMD//\{SCENARIOS_DIR\}/${scenarios_dir}}"
        else
            case "$toolchain" in
                node)   dir_cmd="npx vitest run ${scenarios_dir} 2>&1 || npx jest ${scenarios_dir} 2>&1" ;;
                python) dir_cmd="pytest ${scenarios_dir} -v 2>&1" ;;
                go)     dir_cmd="" ;;
                rust)   dir_cmd="" ;;
                *)      dir_cmd="" ;;
            esac
        fi

        if [[ -z "$dir_cmd" ]]; then
            echo "  WARNING: No test command available for toolchain '${toolchain}'. Skipping multi-run."
            cat > "$counts_file" <<EOF
SCENARIO_PASSED=0
SCENARIO_FAILED=0
SCENARIO_TOTAL=${#files[@]}
SCENARIO_RATE=0
SATISFACTION_SCORE=0
MULTI_RUN_N=${multi_n}
MULTI_RUN_K=${multi_k}
EOF
            return
        fi

        echo "  Multi-run directory-level (${multi_n}x, k=${multi_k})..."
        local mr_rc=0
        _df_multi_run_file "$dir_cmd" "$multi_n" "$multi_k" || mr_rc=$?

        if [[ $mr_rc -eq 0 ]]; then
            total_passed=${#files[@]}
            total_failed=0
            echo "    PASS (${_MR_SUCCESSES}/${multi_n} successes)"
        else
            total_passed=0
            total_failed=${#files[@]}
            echo "    FAIL (${_MR_SUCCESSES}/${multi_n} successes, needed ${multi_k})"
        fi

        # Assign all files to their categories (all pass or all fail)
        for filepath in "${files[@]}"; do
            local fname
            fname="$(basename "$filepath")"
            local prefix="${fname%%-*}"
            if [[ ! "$prefix" =~ ^[0-9][0-9]$ ]]; then
                prefix="99"
            fi
            local weight
            weight=$(_df_layer_weight "$filepath")
            cat_total["$prefix"]=$(( ${cat_total["$prefix"]:-0} + 1 ))
            cat_weight["$prefix"]="$weight"
            if [[ $mr_rc -eq 0 ]]; then
                cat_passed["$prefix"]=$(( ${cat_passed["$prefix"]:-0} + 1 ))
            fi
        done
    fi

    # Compute satisfaction score (Formula B: normalize-then-weight)
    local total=$((total_passed + total_failed))
    local rate=0
    if [[ $total -gt 0 ]]; then
        rate=$((total_passed * 100 / total))
    fi

    local weighted_sum=0
    local weight_sum=0
    local satisfaction=0

    for prefix in "${!cat_total[@]}"; do
        local w="${cat_weight[$prefix]:-100}"
        local p="${cat_passed[$prefix]:-0}"
        local t="${cat_total[$prefix]}"
        if [[ $t -gt 0 ]]; then
            local cat_rate=$((p * 100 / t))
            weighted_sum=$((weighted_sum + w * cat_rate))
            weight_sum=$((weight_sum + w))
        fi
    done

    if [[ $weight_sum -gt 0 ]]; then
        satisfaction=$((weighted_sum / weight_sum))
    fi

    # Write scenario-counts.env
    cat > "$counts_file" <<EOF
SCENARIO_PASSED=${total_passed}
SCENARIO_FAILED=${total_failed}
SCENARIO_TOTAL=${total}
SCENARIO_RATE=${rate}
SATISFACTION_SCORE=${satisfaction}
MULTI_RUN_N=${multi_n}
MULTI_RUN_K=${multi_k}
EOF

    echo "  Scenario counts: ${total_passed}/${total} passed (${rate}%)"
    echo "  Satisfaction score: ${satisfaction}% (weighted)"

    # Write category-breakdown.env
    : > "$cat_file"
    for prefix in $(echo "${!cat_total[@]}" | tr ' ' '\n' | sort); do
        echo "CAT_${prefix}_PASSED=${cat_passed[$prefix]:-0}" >> "$cat_file"
        echo "CAT_${prefix}_TOTAL=${cat_total[$prefix]}" >> "$cat_file"
        echo "CAT_${prefix}_WEIGHT=${cat_weight[$prefix]:-100}" >> "$cat_file"
    done

    # Copy to iteration-stamped file
    cp "$cat_file" "$cat_iter_file"
}

# ==============================================================================
# Per-layer output parsing (single-run mode)
# ==============================================================================

# _df_parse_per_layer_output <raw_output> <spec_name> <iteration>
#   Extracts per-layer pass/fail from test runner output in single-run mode.
#   Writes category-breakdown.env and category-iter{N}.env.
_df_parse_per_layer_output() {
    local raw_output="$1"
    local spec_name="$2"
    local iteration="$3"

    local cat_file=".dark-factory/reports/${spec_name}-category-breakdown.env"
    local cat_iter_file=".dark-factory/reports/${spec_name}-category-iter${iteration}.env"

    # Strip ANSI color codes
    local clean
    clean=$(echo "$raw_output" | sed 's/\x1b\[[0-9;]*m//g')

    local -A cat_passed=() cat_failed=()
    local parsed=false

    # vitest/jest: " PASS path/01-contract.test.ts" or " FAIL path/03-edge-cases.test.ts"
    while IFS= read -r line; do
        local file_part=""
        if [[ "$line" =~ (PASS|FAIL)[[:space:]]+(.+\.(test|spec)\.(ts|js|tsx|jsx|mts|mjs)) ]]; then
            local result="${BASH_REMATCH[1]}"
            file_part="${BASH_REMATCH[2]}"
        elif [[ "$line" =~ (PASS|FAIL)[[:space:]]+(.+) ]]; then
            # Looser match
            local result="${BASH_REMATCH[1]}"
            file_part="${BASH_REMATCH[2]}"
        else
            continue
        fi
        # Extract NN- prefix from filename
        local fname
        fname="$(basename "$file_part")"
        local prefix="${fname%%-*}"
        if [[ ! "$prefix" =~ ^[0-9][0-9]$ ]]; then
            continue
        fi
        parsed=true
        if [[ "$result" == "PASS" ]]; then
            cat_passed["$prefix"]=$(( ${cat_passed["$prefix"]:-0} + 1 ))
        else
            cat_failed["$prefix"]=$(( ${cat_failed["$prefix"]:-0} + 1 ))
        fi
    done <<< "$clean"

    # pytest: "path/test_01_contract.py::test_name PASSED" or "FAILED"
    if [[ "$parsed" != "true" ]]; then
        while IFS= read -r line; do
            if [[ "$line" =~ ([a-zA-Z0-9_/.-]+\.py)::.+[[:space:]]+(PASSED|FAILED) ]]; then
                local fpath="${BASH_REMATCH[1]}"
                local result="${BASH_REMATCH[2]}"
                local fname
                fname="$(basename "$fpath")"
                # Extract NN prefix: test_01_contract.py → 01, 01-contract.py → 01
                local prefix=""
                if [[ "$fname" =~ ^test_([0-9][0-9]) ]]; then
                    prefix="${BASH_REMATCH[1]}"
                elif [[ "$fname" =~ ^([0-9][0-9])- ]]; then
                    prefix="${BASH_REMATCH[1]}"
                fi
                if [[ -z "$prefix" ]]; then
                    continue
                fi
                parsed=true
                if [[ "$result" == "PASSED" ]]; then
                    cat_passed["$prefix"]=$(( ${cat_passed["$prefix"]:-0} + 1 ))
                else
                    cat_failed["$prefix"]=$(( ${cat_failed["$prefix"]:-0} + 1 ))
                fi
            fi
        done <<< "$clean"
    fi

    # If no per-file lines detected, don't write category files
    if [[ "$parsed" != "true" ]]; then
        return
    fi

    # Merge keys and write
    local -A all_prefixes=()
    for k in "${!cat_passed[@]}" "${!cat_failed[@]}"; do
        all_prefixes["$k"]=1
    done

    : > "$cat_file"
    for prefix in $(echo "${!all_prefixes[@]}" | tr ' ' '\n' | sort); do
        local p="${cat_passed[$prefix]:-0}"
        local f="${cat_failed[$prefix]:-0}"
        local t=$((p + f))
        local w
        # Build a dummy filename to get weight
        w=$(_df_layer_weight "${prefix}-dummy.test.ts")
        echo "CAT_${prefix}_PASSED=${p}" >> "$cat_file"
        echo "CAT_${prefix}_TOTAL=${t}" >> "$cat_file"
        echo "CAT_${prefix}_WEIGHT=${w}" >> "$cat_file"
    done

    cp "$cat_file" "$cat_iter_file"
}

# ==============================================================================
# Trajectory classification
# ==============================================================================

# Globals set by _df_classify_trajectory
_DF_TRAJECTORY_TYPE=""
_DF_TRAJECTORY_DESC=""
_DF_TRAJECTORY_RECOMMENDATION=""

# _df_classify_trajectory <pipe_delimited_scores>
#   Classifies the satisfaction score trajectory across iterations.
#   Priority: INSUFFICIENT > IMPROVING > DEGRADING > PLATEAU > OSCILLATING > RECOVERING > MIXED
#   IMPROVING/DEGRADING checked before PLATEAU so small genuine trends aren't misclassified.
_df_classify_trajectory() {
    local raw="$1"

    # Parse into array, filtering empty elements
    local -a scores=()
    IFS='|' read -ra _raw_scores <<< "$raw" || true
    for s in "${_raw_scores[@]+"${_raw_scores[@]}"}"; do
        [[ -z "$s" ]] && continue
        [[ "$s" =~ ^[0-9]+$ ]] || continue
        scores+=("$s")
    done

    if [[ ${#scores[@]} -lt 2 ]]; then
        _DF_TRAJECTORY_TYPE="INSUFFICIENT"
        _DF_TRAJECTORY_DESC="Less than 2 data points — no trend available."
        _DF_TRAJECTORY_RECOMMENDATION="Continue iterating to establish a trend."
        return
    fi

    # Compute deltas
    local -a deltas=()
    local threshold="${TRAJECTORY_PLATEAU_THRESHOLD:-5}"
    local i
    for (( i=1; i<${#scores[@]}; i++ )); do
        deltas+=( $(( scores[i] - scores[i-1] )) )
    done

    # Check IMPROVING first: all deltas positive (> 0)
    local all_positive=true
    for d in "${deltas[@]}"; do
        if [[ $d -le 0 ]]; then
            all_positive=false
            break
        fi
    done

    if [[ "$all_positive" == "true" ]]; then
        _DF_TRAJECTORY_TYPE="IMPROVING"
        _DF_TRAJECTORY_DESC="Scores consistently improving across ${#scores[@]} iterations."
        _DF_TRAJECTORY_RECOMMENDATION="Continue iterating normally."
        return
    fi

    # Check DEGRADING: all deltas negative (< 0)
    local all_negative=true
    for d in "${deltas[@]}"; do
        if [[ $d -ge 0 ]]; then
            all_negative=false
            break
        fi
    done

    if [[ "$all_negative" == "true" ]]; then
        _DF_TRAJECTORY_TYPE="DEGRADING"
        _DF_TRAJECTORY_DESC="Scores consistently degrading across ${#scores[@]} iterations."
        _DF_TRAJECTORY_RECOMMENDATION="Spec may be ambiguous; consider rollback to best iteration."
        return
    fi

    # Check PLATEAU: all deltas within ±threshold (checked after IMPROVING/DEGRADING
    # so genuine small trends aren't misclassified as stagnation)
    local all_plateau=true
    for d in "${deltas[@]}"; do
        local abs_d=$d
        [[ $abs_d -lt 0 ]] && abs_d=$((-abs_d))
        if [[ $abs_d -gt $threshold ]]; then
            all_plateau=false
            break
        fi
    done

    if [[ "$all_plateau" == "true" ]]; then
        _DF_TRAJECTORY_TYPE="PLATEAU"
        _DF_TRAJECTORY_DESC="Scores stable within ±${threshold}% across ${#scores[@]} iterations."
        _DF_TRAJECTORY_RECOMMENDATION="Consider alternative architecture or more specific spec guidance."
        return
    fi

    # Check OSCILLATING: direction alternates
    if [[ ${#deltas[@]} -ge 2 ]]; then
        local oscillating=true
        for (( i=1; i<${#deltas[@]}; i++ )); do
            local prev="${deltas[$((i-1))]}"
            local curr="${deltas[$i]}"
            # Check sign alternation (positive then negative, or vice versa)
            if [[ $prev -gt 0 && $curr -ge 0 ]] || [[ $prev -lt 0 && $curr -le 0 ]] || [[ $prev -eq 0 ]] || [[ $curr -eq 0 ]]; then
                oscillating=false
                break
            fi
        done

        if [[ "$oscillating" == "true" ]]; then
            _DF_TRAJECTORY_TYPE="OSCILLATING"
            _DF_TRAJECTORY_DESC="Scores alternating up and down across iterations."
            _DF_TRAJECTORY_RECOMMENDATION="Narrow scope — implement core first, defer edge cases."
            return
        fi
    fi

    # Check RECOVERING: last delta positive after previous negative
    if [[ ${#deltas[@]} -ge 2 ]]; then
        local last_delta="${deltas[$((${#deltas[@]} - 1))]}"
        local prev_delta="${deltas[$((${#deltas[@]} - 2))]}"
        if [[ $prev_delta -lt 0 ]] && [[ $last_delta -gt 0 ]]; then
            _DF_TRAJECTORY_TYPE="RECOVERING"
            _DF_TRAJECTORY_DESC="Score recovering after previous decline."
            _DF_TRAJECTORY_RECOMMENDATION="Continue — approach is improving."
            return
        fi
    fi

    _DF_TRAJECTORY_TYPE="MIXED"
    _DF_TRAJECTORY_DESC="No clear pattern in score trajectory."
    _DF_TRAJECTORY_RECOMMENDATION="Review iteration feedback for patterns."
}

# ==============================================================================
# Per-layer trajectory
# ==============================================================================

# Globals set by _df_layer_trajectory
_DF_LAYER_TABLE=""
_DF_STUCK_LAYERS=""
_DF_OSCILLATING_LAYERS=""

# Category prefix to human-readable name mapping
_df_category_name() {
    case "$1" in
        01) echo "Contract" ;;
        02) echo "Happy Path" ;;
        03) echo "Edge Cases" ;;
        04) echo "Error Conditions" ;;
        05) echo "State & Sequence" ;;
        06) echo "Security" ;;
        07) echo "Property-Based" ;;
        08) echo "Adversarial" ;;
        *)  echo "Category $1" ;;
    esac
}

# _df_layer_trajectory <spec_name> <current_iter>
#   Builds per-category pass rate table across iterations.
_df_layer_trajectory() {
    local spec_name="$1"
    local current_iter="$2"

    # Collect all category prefixes across all iterations
    local -A all_prefixes=()
    local iter
    for (( iter=1; iter<=current_iter; iter++ )); do
        local iter_file=".dark-factory/reports/${spec_name}-category-iter${iter}.env"
        if [[ -f "$iter_file" ]]; then
            while IFS= read -r line; do
                if [[ "$line" =~ ^CAT_([0-9][0-9])_TOTAL= ]]; then
                    all_prefixes["${BASH_REMATCH[1]}"]=1
                fi
            done < "$iter_file"
        fi
    done

    if [[ ${#all_prefixes[@]} -eq 0 ]]; then
        _DF_LAYER_TABLE=""
        _DF_STUCK_LAYERS=""
        _DF_OSCILLATING_LAYERS=""
        return
    fi

    # Build header
    local header="| Category"
    local separator="|---"
    for (( iter=1; iter<=current_iter; iter++ )); do
        header="${header} | Iter ${iter}"
        separator="${separator}|---"
    done
    header="${header} | Trend |"
    separator="${separator}|---|"

    local table="${header}
${separator}"

    local stuck_layers=""
    local oscillating_layers=""

    for prefix in $(echo "${!all_prefixes[@]}" | tr ' ' '\n' | sort); do
        local name
        name=$(_df_category_name "$prefix")
        local row="| ${name}"
        local -a rates=()

        for (( iter=1; iter<=current_iter; iter++ )); do
            local iter_file=".dark-factory/reports/${spec_name}-category-iter${iter}.env"
            if [[ -f "$iter_file" ]]; then
                local p=0 t=0
                p=$(grep "^CAT_${prefix}_PASSED=" "$iter_file" | cut -d= -f2) || p=0
                t=$(grep "^CAT_${prefix}_TOTAL=" "$iter_file" | cut -d= -f2) || t=0
                if [[ -n "$t" ]] && [[ "$t" -gt 0 ]]; then
                    local r=$((p * 100 / t))
                    row="${row} | ${r}%"
                    rates+=("$r")
                else
                    row="${row} | —"
                fi
            else
                row="${row} | —"
            fi
        done

        # Classify per-layer trend
        local trend="—"
        if [[ ${#rates[@]} -ge 2 ]]; then
            local layer_scores
            layer_scores=$(IFS='|'; echo "${rates[*]}")
            _df_classify_trajectory "$layer_scores"
            trend="$_DF_TRAJECTORY_TYPE"

            if [[ "$trend" == "PLATEAU" ]]; then
                # Check if stuck at 0%
                local all_zero=true
                for r in "${rates[@]}"; do
                    [[ "$r" -gt 0 ]] && all_zero=false
                done
                if [[ "$all_zero" == "true" ]]; then
                    trend="STUCK"
                    stuck_layers="${stuck_layers:+${stuck_layers}, }${name}"
                fi
            elif [[ "$trend" == "OSCILLATING" ]]; then
                oscillating_layers="${oscillating_layers:+${oscillating_layers}, }${name}"
            fi
        fi

        row="${row} | ${trend} |"
        table="${table}
${row}"
    done

    _DF_LAYER_TABLE="$table"
    _DF_STUCK_LAYERS="$stuck_layers"
    _DF_OSCILLATING_LAYERS="$oscillating_layers"
}

# ==============================================================================
# Strategy recommendation
# ==============================================================================

# Globals set by _df_strategy_recommendation
_DF_STRATEGY_RECOMMENDATION=""

# _df_strategy_recommendation <trajectory_type>
_df_strategy_recommendation() {
    local trajectory="$1"
    local rec=""

    case "$trajectory" in
        IMPROVING)
            rec="Continue iterating normally — scores are improving."
            ;;
        PLATEAU)
            rec="Scores are plateauing. Consider alternative architecture or more specific spec guidance."
            ;;
        OSCILLATING)
            rec="Scores are oscillating — fixing one area breaks another. Narrow scope: implement core first, defer edge cases."
            ;;
        DEGRADING)
            rec="Scores are degrading. The spec may be ambiguous or the implementation approach is diverging. Consider rollback to best iteration."
            ;;
        RECOVERING)
            rec="Scores are recovering after a dip. Continue iterating — the approach is improving."
            ;;
        STUCK)
            rec="No progress being made. Consider spec revision or human intervention."
            ;;
        *)
            rec="No clear trajectory pattern. Review iteration feedback for actionable patterns."
            ;;
    esac

    # Append layer-specific notes
    if [[ -n "${_DF_STUCK_LAYERS:-}" ]]; then
        rec="${rec} STUCK layers (zero progress): ${_DF_STUCK_LAYERS}."
    fi
    if [[ -n "${_DF_OSCILLATING_LAYERS:-}" ]]; then
        rec="${rec} OSCILLATING layers: ${_DF_OSCILLATING_LAYERS}."
    fi

    _DF_STRATEGY_RECOMMENDATION="$rec"
}

# ==============================================================================
# Oracle analysis builder
# ==============================================================================

# Globals set by _df_build_oracle_analysis
_DF_ORACLE_ANALYSIS=""

# _df_build_oracle_analysis <iteration> <pass_rate> <passed> <total> <floor>
_df_build_oracle_analysis() {
    local iteration="$1"
    local pass_rate="$2"
    local passed="$3"
    local total="$4"
    local floor="$5"

    local analysis="## Satisfaction Analysis
- Satisfaction score: ${SATISFACTION_SCORE:-${pass_rate}}% (weighted) | Pass rate: ${pass_rate}% (${passed}/${total})"

    # Multi-run info
    local mn="${SCENARIO_MULTI_RUN_N:-1}"
    local mk="${SCENARIO_MULTI_RUN_K:-1}"
    if [[ "$mn" -gt 1 ]]; then
        analysis="${analysis}
- Multi-run: N=${mn}, K=${mk}"
    fi

    analysis="${analysis}
- Floor: ${floor}%"

    # History
    if [[ -n "${_DF_SATISFACTION_SCORES:-}" ]]; then
        local history
        history=$(echo "${_DF_SATISFACTION_SCORES}" | tr '|' ' → ')
        analysis="${analysis}
- History: ${history}"
    fi

    # Trajectory
    if [[ -n "${_DF_TRAJECTORY_TYPE:-}" ]]; then
        analysis="${analysis}
- Trajectory: ${_DF_TRAJECTORY_TYPE}"
    fi

    # Per-category breakdown
    if [[ -n "${_DF_LAYER_TABLE:-}" ]]; then
        analysis="${analysis}

## Per-Category Breakdown
${_DF_LAYER_TABLE}"
    fi

    # Strategy recommendation
    if [[ -n "${_DF_STRATEGY_RECOMMENDATION:-}" ]]; then
        analysis="${analysis}

## Strategy Recommendation
${_DF_STRATEGY_RECOMMENDATION}"
    fi

    _DF_ORACLE_ANALYSIS="$analysis"
}

# ==============================================================================
# Config validation
# ==============================================================================

# _df_validate_satisfaction_config
#   Validates Phase 3 config variables. Returns 1 on failure.
_df_validate_satisfaction_config() {
    local n="${SCENARIO_MULTI_RUN_N:-1}"
    local k="${SCENARIO_MULTI_RUN_K:-1}"

    # N must be positive integer
    if [[ ! "$n" =~ ^[1-9][0-9]*$ ]]; then
        echo "ERROR: SCENARIO_MULTI_RUN_N must be a positive integer, got: '$n'"
        return 1
    fi

    # K must be positive integer
    if [[ ! "$k" =~ ^[1-9][0-9]*$ ]]; then
        echo "ERROR: SCENARIO_MULTI_RUN_K must be a positive integer, got: '$k'"
        return 1
    fi

    # K <= N
    if [[ "$k" -gt "$n" ]]; then
        echo "ERROR: SCENARIO_MULTI_RUN_K ($k) must be <= SCENARIO_MULTI_RUN_N ($n)"
        return 1
    fi

    # MIN_SATISFACTION_SCORE in range 0-100
    local ms="${MIN_SATISFACTION_SCORE:-0}"
    if [[ ! "$ms" =~ ^[0-9]+$ ]] || [[ "$ms" -gt 100 ]]; then
        echo "ERROR: MIN_SATISFACTION_SCORE must be 0-100, got: '$ms'"
        return 1
    fi

    # All SCENARIO_WEIGHT_* must be positive integers
    local weight_vars=(
        SCENARIO_WEIGHT_CONTRACT SCENARIO_WEIGHT_HAPPY_PATH
        SCENARIO_WEIGHT_EDGE_CASES SCENARIO_WEIGHT_ERROR_CONDITIONS
        SCENARIO_WEIGHT_STATE_SEQUENCE SCENARIO_WEIGHT_SECURITY
        SCENARIO_WEIGHT_PROPERTY_BASED SCENARIO_WEIGHT_ADVERSARIAL
    )
    for var in "${weight_vars[@]}"; do
        local val="${!var:-}"
        if [[ -n "$val" ]]; then
            if [[ ! "$val" =~ ^[1-9][0-9]*$ ]]; then
                echo "ERROR: $var must be a positive integer, got: '$val'"
                return 1
            fi
        fi
    done

    # TRAJECTORY_PLATEAU_THRESHOLD must be non-negative integer
    local tpt="${TRAJECTORY_PLATEAU_THRESHOLD:-5}"
    if [[ ! "$tpt" =~ ^[0-9]+$ ]]; then
        echo "ERROR: TRAJECTORY_PLATEAU_THRESHOLD must be a non-negative integer, got: '$tpt'"
        return 1
    fi

    return 0
}
