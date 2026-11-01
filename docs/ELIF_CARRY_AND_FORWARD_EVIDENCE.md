# Elif Carry — two-leg PAPER boundary

Elif is a pair strategy, not a directional single-instrument Strategy. It is
therefore deliberately **not** added to `radar/unified/strategies/REGISTRY`:
the generic DecisionEngine/UnifiedTrader single-leg open path cannot safely
represent spot-long + perpetual-short as one order.

Implemented components:

- `ElifCarryStrategy.evaluate_pair`: requires fresh spot and perpetual BBOs,
  current positive funding, fresh positive 30-day funding history, and a
  positive projected net edge after four-leg fees/spread/slippage.
- `CarryPairPaper`: isolated durable ledger, exact matched base quantities,
  explicit orderbook depth on both legs, four-leg entry/exit costs, funding
  settlement sign, idempotent pair IDs, duplicate funding rejection and
  fail-closed reconciliation.
- UnifiedTrader owns both components and publishes the isolated ledger state
  to its real status file. The carry config is explicitly `enabled: false`.

Important limits before activation:

1. The production market adapter still needs a trusted spot BBO stream and a
   timestamped 30-day funding history source in the UnifiedTrader runner.
2. A pair candidate is analysis only. It is not passed to the single-leg
   execution adapter. Pair opening must be explicitly orchestrated through
   `CarryPairPaper.open_pair` by UnifiedTrader after all inputs are proven.
3. The simulator requires depth for both entry and exit legs; top-of-book-only
   quotes are rejected rather than assuming a fill.
4. Separate carry capital/margin allocation and dashboard detail are still
   operational acceptance items. No carry position is opened by default.

Tests use clearly synthetic fixtures and cover disabled-by-default, candidate
freshness/funding, matched quantities, depth, idempotency, persistence, funding,
four-leg close accounting, missing exit legs and reconciliation. Synthetic
fixtures are test coverage, not performance evidence.

# Forward PAPER evidence

`ForwardPaperEvidence` is wired into every UnifiedTrader step. It writes
`forward_paper_evidence.json` from real, fresh quote + healthy websocket
heartbeats only. A gap above 30 minutes restarts the observation window.
Only closed ledger rows with `execution_scope=unified`, valid entry/exit
timestamps, and unique open order IDs count toward the 100-trade threshold.
Legacy, shadow, backtest, incomplete, or backdated rows are excluded.

The gate becomes ready only after both 14 elapsed days with a current healthy
heartbeat and 100 eligible closed Unified PAPER trades. Code/tests cannot
manufacture this evidence; it accrues only while the PAPER runner actually
operates on trusted live data.
