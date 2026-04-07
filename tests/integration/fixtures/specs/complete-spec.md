# Spec: Add Multiply Function to Calculator

<!-- dark-factory-metadata
feature-id: F-1
spec_type: feature
-->

## Overview
Add a multiply function to the calculator module.

## Functional Requirements
- FR-1: `calculator.multiply(a, b)` returns `a * b` for integer inputs
- FR-2: `calculator.multiply(a, b)` returns `a * b` for float inputs
- FR-3: Handles negative numbers correctly

## Edge Cases
- EC-1: `multiply(0, x)` returns `0` for any `x`
- EC-2: `multiply(1, x)` returns `x` for any `x`
- EC-3: Very large numbers do not overflow (Python handles arbitrary precision)

## Error Conditions
- ERR-1: Non-numeric input raises `TypeError` with descriptive message
- ERR-2: `None` input raises `TypeError`

## Performance Requirements
- PERF-1: Completes in <1ms for standard inputs

## Security Considerations
- SEC-1: No code injection via string inputs (type checking prevents this)

## Source Files
- `src/calculator.py`

## Test Strategy
- Unit tests via pytest
- Scenarios cover all FR, EC, ERR categories
