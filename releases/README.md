# Recovery baselines

These records identify scoped, pre-release recovery references, not blanket
production or trading certification. API contract versions are independent.

| Baseline | Scope | Limits |
| --- | --- | --- |
| [2026.11.14.1](ftmo-readonly-baseline-2026.11.14.1.md) | FTMO Demo configuration, recovery procedure and redacted GET diagnostic | UTC freshness, historical completeness, deployment reproducibility and mutation remain uncertified |

The [manifest](ftmo-readonly-baseline-2026.11.14.1.json) is machine-readable.
The [configuration example](../service/config/examples/ftmo-demo.yaml) requires
explicit selection; it never replaces default settings automatically.
The [diagnostic](../mt5docker/probe_execution_readonly.py) makes GET requests only.
Read the baseline limitations and retain operator approvals before using it.
