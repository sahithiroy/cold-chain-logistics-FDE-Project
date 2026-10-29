-- 1. How many rows? (expect 32065)
SELECT COUNT(*) AS total_rows FROM dbo.TBL_SC_FLEET_HIST_RAW;

-- 2. Look at 10 rows
SELECT TOP 10 * FROM dbo.TBL_SC_FLEET_HIST_RAW;

-- 3. Rows per risk level
SELECT RISK_CLS_TXT, COUNT(*) AS cnt
FROM dbo.TBL_SC_FLEET_HIST_RAW
GROUP BY RISK_CLS_TXT;

-- 4. Readings above 8°C (too warm for fresh produce)
SELECT COUNT(*) AS too_warm FROM dbo.TBL_SC_FLEET_HIST_RAW WHERE IOT_TEMP_VAL_C > 8;