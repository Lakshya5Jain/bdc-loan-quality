# Project documents

Written 2026-09-09. Both are self-contained HTML pages; open them in a browser.

- `bdc-signal-book.html` — the detailed reference: how every number is built from the SEC filings,
  every threshold in the code, the return backtest tables, and the live ranking.
  Published copy: https://claude.ai/code/artifact/b045313f-dffc-43ce-b483-22690df12615
- `bdc-plain-english.html` — the same material explained simply with a worked example (Maple Lending).
  Published copy: https://claude.ai/code/artifact/bbec02c2-656a-4e1f-b709-82d08378af33

Regenerate the numbers with `uv run soi build all && uv run soi backtest`; the tables behind the
documents are `signals.bt_summary`, `signals.bt_periods` and `signals.bt_latest`.
