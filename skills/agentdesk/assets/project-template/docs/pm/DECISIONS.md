# Decision Log

Append durable decisions. Do not rewrite history; add a new row that supersedes an old decision.

Consultation synthesis is not a decision. A consultation-derived row requires an explicit User / Owner confirmation and must cite both `consultation_id` and `confirmation_id` in `Source / confirmation`. Unconfirmed, rejected, or deferred consultation outcomes stay in their consultation records and must not be appended here. A consultation confirmation does not authorize execution, acceptance, merge, release, or deployment.

| ID | Date | Decision | Rationale | Source / confirmation | Supersedes |
| --- | --- | --- | --- | --- | --- |
