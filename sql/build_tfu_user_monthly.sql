-- build_tfu_user_monthly.sql
--
-- Builds the tfu_user_monthly feature table: one row per (user_id, month)
-- containing the monthly TFU target and rolling user-level features.
--
-- TODO: paste the full corrected SQL from the prior session into this
-- file. This is a placeholder so the repo scaffold compiles.

CREATE OR REPLACE TABLE tfu_user_monthly AS
SELECT
    user_id,
    DATE_TRUNC('month', event_ts) AS month,
    SUM(fee_amount)               AS tfu
FROM raw.events
GROUP BY 1, 2;
