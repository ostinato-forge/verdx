# Assessment: sample/ch04/ge01

- **Run**: `run-001`
- **Timestamp**: 2026-04-30T12:00:00Z
- **Content hash**: `sha256:abc123`

## Coverage and Confidence

- **Coverage**: full
- **Confidence**: guarded

## Clean Summary

- **Checks run**: 15
- **Checks passed**: 14
- **Validators clean**: 0

## Requires Human Decision

### [Requires Human Decision] Lab solution references deprecated API endpoint

- **Rule**: `api-deprecation`
- **Category**: correctness
- **Severity**: high
- **Confidence**: confirmed
- **Location**: `exercise:ge01/step:5`
- **Validator**: yaml-lint v1.0.0
- **Four-Eyes**: satisfied (independent)

**Evidence:**

- api-check/deprecation: POST /v1/users is deprecated since v2.3

**Invalidating conditions:**

- API endpoint is restored in a future version

## Score

**70/100**

---

Assessment: `verdx.assessment.v1`  
Run: `run-001`  
Target: `sample/ch04/ge01`  
Hash: `sha256:abc123`
