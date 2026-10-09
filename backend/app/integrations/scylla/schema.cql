-- Price history for the charts: one partition per market and UTC day (SPEC §9).
--
-- Run by QuoteHistoryStore.ensure_schema(), which creates the keyspace first and puts
-- its name in front of the table. To run this by hand in cqlsh, USE the keyspace first.
CREATE TABLE IF NOT EXISTS quotes_by_market_day (
  market_id uuid, day date, ts timestamp, version bigint,
  p_yes_micro bigint, q_yes_micro bigint, q_no_micro bigint, trade_id uuid,
  PRIMARY KEY ((market_id, day), ts, version)
) WITH CLUSTERING ORDER BY (ts DESC, version DESC)
  AND compaction = {'class':'TimeWindowCompactionStrategy','compaction_window_unit':'DAYS','compaction_window_size':1};
