#!/usr/bin/env bash
# scripts/lib/barriers.sh — Information barrier configuration
#
# Provides: configure_barriers
# Requires globals: SOURCE_DIRS (or SOURCE_DIR), set after config is sourced.
#
# Sets 12 globals consumed by run_agent() at call time:
#   IMPLEMENTER_DENY, IMPLEMENTER_BARRIER
#   SCENARIO_ARCH_DENY, SCENARIO_ARCH_BARRIER
#   EVALUATOR_DENY, EVALUATOR_BARRIER
#   REVIEWER_DENY, REVIEWER_BARRIER
#   SPEC_VALIDATOR_DENY, SPEC_VALIDATOR_BARRIER
#   ORACLE_DENY, ESCALATION_DENY

[[ -n "${_DF_LIB_BARRIERS_LOADED:-}" ]] && return 0
_DF_LIB_BARRIERS_LOADED=1

# configure_barriers
#   Must be called after config sourcing (needs SOURCE_DIRS/SOURCE_DIR).
configure_barriers() {
    # File-access tool restrictions (reliable — path-based patterns)
    IMPLEMENTER_DENY="Read(.dark-factory/scenarios/**),Edit(.dark-factory/scenarios/**),Write(.dark-factory/scenarios/**),Grep(.dark-factory/scenarios/**),Glob(.dark-factory/scenarios/**),Read(.dark-factory/reports/**),Grep(.dark-factory/reports/**),Glob(.dark-factory/reports/**),Read(.dark-factory/audit/**),Grep(.dark-factory/audit/**),Glob(.dark-factory/audit/**),Read(.dark-factory/state/**),Grep(.dark-factory/state/**),Glob(.dark-factory/state/**),WebSearch,WebFetch"

    # Build deny list and barrier prompt covering all configured source directories
    local -a _sa_deny_parts=()
    local -a _sa_dir_labels=()
    local _dir
    for _dir in ${SOURCE_DIRS:-${SOURCE_DIR:-src}}; do
        _sa_deny_parts+=("Read(${_dir}/**)" "Grep(${_dir}/**)" "Glob(${_dir}/**)")
        _sa_dir_labels+=("${_dir}/")
    done
    SCENARIO_ARCH_DENY="$(IFS=,; echo "${_sa_deny_parts[*]}"),WebSearch,WebFetch"
    SCENARIO_ARCH_BARRIER="CRITICAL SECURITY RULE: You MUST NOT read, access, or examine any implementation source code in $(IFS=', '; echo "${_sa_dir_labels[*]}"). Derive all scenarios purely from the specification. If you encounter implementation details, ignore them."

    IMPLEMENTER_BARRIER="CRITICAL SECURITY RULE: You MUST NOT read, access, or examine any file in .dark-factory/scenarios/, .dark-factory/reports/, .dark-factory/audit/, or .dark-factory/state/. These are information barriers. Work only from the spec and Oracle feedback. If you see scenario content in error messages, do not use it to guide implementation."

    REVIEWER_DENY="Read(.dark-factory/scenarios/**),Edit(.dark-factory/scenarios/**),Write(.dark-factory/scenarios/**),Grep(.dark-factory/scenarios/**),Glob(.dark-factory/scenarios/**),WebSearch,WebFetch"

    REVIEWER_BARRIER="CRITICAL SECURITY RULE: You MUST NOT read scenario source files in .dark-factory/scenarios/. Evaluate test results from the quality gate report only. Share test RESULTS (pass/fail, error messages) but never test SOURCE CODE in your review."

    SPEC_VALIDATOR_DENY="Read(.dark-factory/scenarios/**),Grep(.dark-factory/scenarios/**),Glob(.dark-factory/scenarios/**),WebSearch,WebFetch"

    SPEC_VALIDATOR_BARRIER="SECURITY RULE: You MUST NOT read test scenario files in .dark-factory/scenarios/. Evaluate the specification on its own merits without reference to existing test scenarios."

    # Evaluator has same source-code deny as scenario-architect
    EVALUATOR_DENY="$SCENARIO_ARCH_DENY"
    EVALUATOR_BARRIER="CRITICAL SECURITY RULE: You MUST NOT read, access, or examine any implementation source code in $(IFS=', '; echo "${_sa_dir_labels[*]}"). Evaluate application behavior through HTTP requests only. If you encounter implementation details, ignore them."

    ORACLE_DENY="WebSearch,WebFetch"
    ESCALATION_DENY="WebSearch,WebFetch"
}
