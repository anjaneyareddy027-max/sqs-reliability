# Live lite ↔ localsim fidelity (not confirmatory n>1)

Relative ranks agree: **True**.
Absolute recovery seconds are **not** comparable (live 200 orders vs localsim ~3600).

| cell | live loss/dup/DLQ/rec | sim n | sim dup mean | sim DLQ mean | sim rec mean |
|---|---|---:|---:|---:|---:|
| consumer_kill VT30 MRC5 (exact) | 0.0/0.015/0.0/2.343 | 40 | 0.0975 | 0.0000 | 33.750 |
| consumer_kill VT90 MRC5 (nearest_vt=60) | 0.0/0.005/0.0/7.532 | 10 | 0.0773 | 0.0000 | 61.000 |
| unhandled_error VT30 MRC1 (exact) | 0.0/0.0/0.16/— | 16 | 0.0000 | 0.2929 | 31.875 |
| unhandled_error VT30 MRC5 (exact) | 0.0/0.05/0.0/28.506 | 20 | 0.1321 | 0.0000 | 33.000 |
