"""
Regenerate datasources/company_tickers.tsv from SEC's company_tickers.json.

    python scripts/refresh_tickers.py              # fetch from SEC
    python scripts/refresh_tickers.py tickers.json # from a copy already downloaded

The snapshot is what datasources/tickers.py reads to tell which company a chat question is
about, so filing a run under a ticker never needs a network call (or a test that makes one).
SEC lists companies largest first, and the order is kept: where two names match, the first
wins. Stdlib only. SEC's fair-access policy asks for a contact in the User-Agent; set
SEC_USER_AGENT as for datasources/edgar.py.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from datasources.edgar import _TICKER_MAP_URL, http_get_json  # noqa: E402

OUT = ROOT / "datasources" / "company_tickers.tsv"


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    else:
        data = http_get_json(_TICKER_MAP_URL)
    rows = [(str(e["ticker"]).upper(), " ".join(str(e["title"]).split()))
            for e in data.values() if e.get("ticker") and e.get("title")]
    header = (f"# Source: {_TICKER_MAP_URL}, fetched {date.today().isoformat()}, "
              f"{len(rows)} entries, SEC's order (largest first). "
              "Regenerate with scripts/refresh_tickers.py.\n")
    OUT.write_text(header + "".join(f"{t}\t{n}\n" for t, n in rows), encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(rows)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
