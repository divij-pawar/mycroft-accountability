# Analysis format measurement

Measured 2026-10-26T05:40:01Z with prompt `analysis-v2`, temperature 0.7, one seed per run, on AAPL (data as of 2026-10-23).

This is format compliance and figure quoting by the models listed, on these tickers and this prompt. It does not say whether any signal is right, and one ticker is a narrow sample.

| Model | Runs | Passed first try | Recovered on retry | Halted | Errors | Median time |
|---|---|---|---|---|---|---|
| `llama3.2` | 10 | 8 | 0 | 2 | 0 | 61.0 s |

## `llama3.2`

- First reply passed the format in **8 of 10** completed runs (80%); a signal was produced in **8** (80%) after the one retry.
- Why replies failed (per failed attempt): the <signal> block is not valid JSON x1; the reply has no <signal> block directly after <conclusion> x1; the reply has none of the <thought_log> x1; the reply was written without calling any data tool x1.
- Figures it cited that could be checked: 5; 0 grounded, 0 mismatched, 5 unreferenced; 0 of 4 runs that cited figures had at least one mismatch.
- Median tool calls per completed run: 1.0.

## Runs

| # | Model | Ticker | Question | Outcome | Attempts | Tool calls | Time | Grounded | Why a reply failed |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `llama3.2` | AAPL | Should I buy AAPL? | passed_first | 1 | 1 | 47.4 s | 0/0 |  |
| 2 | `llama3.2` | AAPL | What is your view on AAPL over the next 6 months? | passed_first | 1 | 1 | 165.3 s | 0/1 |  |
| 3 | `llama3.2` | AAPL | Is AAPL a good long-term hold? | halted | 2 | 1 | 59.0 s | n/a | the <signal> block is not valid JSON; the reply has no <signal> block directly after <conclusion> |
| 4 | `llama3.2` | AAPL | Give me a short-term signal for AAPL. | passed_first | 1 | 1 | 40.2 s | 0/0 |  |
| 5 | `llama3.2` | AAPL | Would you sell AAPL here? | passed_first | 1 | 1 | 41.3 s | 0/0 |  |
| 6 | `llama3.2` | AAPL | Should I buy AAPL? | passed_first | 1 | 1 | 52.7 s | 0/1 |  |
| 7 | `llama3.2` | AAPL | What is your view on AAPL over the next 6 months? | halted | 2 | 1 | 222.4 s | n/a | the reply has none of the <thought_log>; the reply was written without calling any data tool |
| 8 | `llama3.2` | AAPL | Is AAPL a good long-term hold? | passed_first | 1 | 1 | 164.1 s | 0/2 |  |
| 9 | `llama3.2` | AAPL | Give me a short-term signal for AAPL. | passed_first | 1 | 1 | 62.9 s | 0/0 |  |
| 10 | `llama3.2` | AAPL | Would you sell AAPL here? | passed_first | 1 | 1 | 175.6 s | 0/1 |  |
