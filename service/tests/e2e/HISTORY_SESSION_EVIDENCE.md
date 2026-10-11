# History freshness verification input

`HISTORY_SESSION_EVIDENCE_PATH` optionally points to a non-secret JSON file
containing `symbols`, keyed by logical symbol. No default calendar is supplied.
Absent, incomplete or expired evidence deliberately fails both history freshness
checks with `unknown`; this is a verification blocker, not proof of worker failure.

Each symbol requires `source`, `observed_at` (absolute timestamp), `timezone`
(IANA zone), `timezone_source`, `exceptions_source`, `valid_from`, `valid_until`
(absolute timestamps), and `weekly`: weekday 0–6 mapped to pairs of local
`HH:MM` opening/closing times. Optional `exceptions` maps local ISO dates to
replacement pairs, including an empty list for full closure. Overnight sessions
are unsupported and fail closed (including a `24:00` boundary); do not guess
their representation. All seven weekdays must be explicit, with empty lists for
closed days. Evidence validity must include the previous session used for the
closed check. Source labels are operator assertions, not automatic verification.

Do not populate production input with the synthetic unit fixtures. GUI weekly
Sessions alone does not establish timezone/DST or dated holiday exceptions.
The current UTC inference conflicts with general published broker information;
resolve that conflict for this exact demo server before certifying a calendar.

Open checks retain existing per-timeframe age bounds. Closed checks require the
latest cached bar to lie within that bound before the last proven close; a week
of missing bars is not excused by today's closure. More than eight days without
a covered session yields unknown. This module changes no Execution wire path,
quote freshness bound, grant or gate.
