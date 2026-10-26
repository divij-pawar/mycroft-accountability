# Planted-error audit: analyses

Bundle `sha256:1961b487fb3a` (AAPL, data as of 2026-10-05). 34 figures an agent could correctly cite from the four recorded tool calls, 34 of which Tier 1 passes as written; each had one error planted at a time, and the same checks were rerun.

This measures the detector on figures whose truth is known. It says nothing about how often a real agent makes these errors, and a figure the checker can't read can't be tested on (listed below).

**Errors:** 227 of 229 caught (99%), 2 missed.  
**Controls** (should not be caught): 0 of 68 wrongly caught.

| Planted error | Trials | Caught | Missed |
|---|---|---|---|
| `plus_1pct` | 34 | 34 | 0 |
| `plus_10pct` | 34 | 34 | 0 |
| `minus_10pct` | 34 | 34 | 0 |
| `digit_swap` | 34 | 34 | 0 |
| `unit_slip` | 4 | 4 | 0 |
| `sign_flip` | 3 | 3 | 0 |
| `wrong_call` | 34 | 34 | 0 |
| `missing_call` | 34 | 34 | 0 |
| `neighbour_value` | 18 | 16 | 2 |

| Control | Trials | Kept | Wrongly caught |
|---|---|---|---|
| `same_value` | 34 | 34 | 0 |
| `rounded` | 34 | 34 | 0 |

| Figure group | Caught | Missed |
|---|---|---|
| filing | 40 | 0 |
| indicator | 151 | 2 |
| price | 36 | 0 |

## Missed (2)

By planted error: `neighbour_value` x2.

| Planted error | Figure | True value | Written as | Why Tier 1 let it through |
|---|---|---|---|---|
| `neighbour_value` | `macd_12_26_9_macd` | 3.4667 | 4.5276 USD, call 2 | matches result.results[6].macd.values.signal in call 2 |
| `neighbour_value` | `macd_12_26_9_signal` | 4.5276 | 3.4667 USD, call 2 | matches result.results[6].macd.values.macd in call 2 |
