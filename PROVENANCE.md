# Source history

Verdx began as an extracted verification engine. The public repository does not
record the source repository or source revision. Those fields remain unknown.
They must be confirmed before describing this repository as current with its
source project.

## Confirmed public history

| Revision | Date | Recorded change |
| --- | --- | --- |
| `5d2e88d` | 2026-07-10 | Initial engine snapshot. |
| `70bf713` | 2026-07-10 | Apache-2.0 license. |
| `0538582` | 2026-11-06 | README status banner. |

The November banner described a September extraction. The public commit dates
do not establish that date. The history above records what Git confirms.
Existing `qu:v1` finding identifiers are retained for compatibility; they do not
establish the source repository on their own.

## Acceptance workflow design references

These projects informed the acceptance workflow. They are design references,
not a claim that the implementation was copied from them:

| Project and reviewed revision | Applied idea |
| --- | --- |
| [Usabl](https://github.com/usabl-dev/usabl/tree/baa2c504ee1b4d44c8ab5baa66be40dc43d58407) | Missing coverage is distinct from a passing check. |
| [Siglum](https://github.com/siglum-dev/siglum/tree/aced206a161ec3a2711a2bb4053a22bdc5093b8f) | Bind a decision to its policy, checker results, evidence, and verdict. |

Usabl's existing-debt handling and Siglum's independent enforcement and signing
are not implemented by this acceptance workflow. They remain separate features
to assess against real project needs.

## How to track later source changes

For each adopted change, record the source repository, immutable revision,
behavior being adopted, relevant tests, and destination commit in the pull
request. Record rejected or deferred changes with their reason. A matching test
result is evidence about that behavior, not proof of complete source parity.
