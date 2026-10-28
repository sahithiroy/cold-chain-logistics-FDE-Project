/* =====================================================================
   Phase 2 - Data security: clean view + read-only agent login

   Run as the admin user (sa) on the legacy-mssql connection.
   Safe to run more than once.

   Result:
     FDE_VIEWS.VW_ACTIVE_FLEET  clean, readable view of the active fleet
     USR_FDE_RO                 login the AI agent uses:
                                  - CAN    read FDE_VIEWS.VW_ACTIVE_FLEET
                                  - CANNOT read or change dbo.TBL_SC_FLEET_HIST_RAW
                                  - CANNOT create, change or drop anything
   ===================================================================== */

USE master;
GO

/* ---------------------------------------------------------------------
   1. Schema for everything the agent is allowed to see.
      Owned by dbo, the same owner as the raw table. This "ownership chain"
      lets the view read the raw table on the agent's behalf, even though
      the agent itself is denied access to the raw table.
   --------------------------------------------------------------------- */
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'FDE_VIEWS')
    EXEC ('CREATE SCHEMA FDE_VIEWS AUTHORIZATION dbo');
GO

/* ---------------------------------------------------------------------
   2. Clean view over the legacy table
      - Renames cryptic legacy columns to readable names
      - Converts the text timestamp to a real DATETIME2
      - Adds a readable shipment ID (the source has none; each reading
        has a unique hourly timestamp)
      - Keeps only "active" readings: the last 30 days of data.
        The dataset is historical (ends 2024-08-29), so "now" is the
        latest reading. In a live system this would be SYSUTCDATETIME().
   --------------------------------------------------------------------- */
CREATE OR ALTER VIEW FDE_VIEWS.VW_ACTIVE_FLEET
AS
WITH readings AS (
    SELECT
        TRY_CONVERT(DATETIME2(0), TS_UTC, 120) AS reading_time_utc,
        V_LAT, V_LON, IOT_TEMP_VAL_C, CGO_COND_CD, RISK_CLS_TXT,
        DELAY_PROB_DEC, PRT_CNG_LVL, RT_RSK_IDX
    FROM dbo.TBL_SC_FLEET_HIST_RAW
    WHERE SYS_INGEST_FLAG = 'Y'
)
SELECT
    'SHP-' + FORMAT(r.reading_time_utc, 'yyyyMMddHH')  AS shipment_id,
    r.reading_time_utc,
    CAST(r.V_LAT          AS DECIMAL(9, 6))  AS latitude,
    CAST(r.V_LON          AS DECIMAL(9, 6))  AS longitude,
    CAST(r.IOT_TEMP_VAL_C AS DECIMAL(5, 2))  AS cargo_temp_c,
    CAST(r.CGO_COND_CD    AS DECIMAL(4, 3))  AS cargo_condition_score,  -- 0 (bad) to 1 (good)
    CAST(r.RISK_CLS_TXT   AS VARCHAR(20))    AS risk_classification,    -- Low / Moderate / High Risk
    CAST(r.DELAY_PROB_DEC AS DECIMAL(4, 3))  AS delay_probability,      -- 0 to 1
    CAST(r.PRT_CNG_LVL    AS DECIMAL(4, 2))  AS port_congestion_level,  -- 0 to 10
    CAST(r.RT_RSK_IDX     AS DECIMAL(4, 2))  AS route_risk_level        -- 0 to 10
FROM readings AS r
WHERE r.reading_time_utc >= DATEADD(DAY, -30, (SELECT MAX(reading_time_utc) FROM readings));
GO

/* ---------------------------------------------------------------------
   3. Login + database user for the AI agent
   --------------------------------------------------------------------- */
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = 'USR_FDE_RO')
    CREATE LOGIN USR_FDE_RO
        WITH PASSWORD = 'AgentPassword2026!',
             DEFAULT_DATABASE = master,
             CHECK_POLICY = ON;
GO

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = 'USR_FDE_RO')
    CREATE USER USR_FDE_RO FOR LOGIN USR_FDE_RO;
GO

/* ---------------------------------------------------------------------
   4. Permissions (least privilege)
      GRANT only what the agent needs. DENY on the raw table makes the
      rule explicit: even if someone later grants broader rights by
      mistake, DENY wins.
   --------------------------------------------------------------------- */
-- View: read only
GRANT SELECT ON OBJECT::FDE_VIEWS.VW_ACTIVE_FLEET TO USR_FDE_RO;
DENY INSERT, UPDATE, DELETE, ALTER ON OBJECT::FDE_VIEWS.VW_ACTIVE_FLEET TO USR_FDE_RO;

-- Raw legacy table: no access at all
DENY SELECT, INSERT, UPDATE, DELETE, ALTER ON OBJECT::dbo.TBL_SC_FLEET_HIST_RAW TO USR_FDE_RO;
GO

/* ---------------------------------------------------------------------
   5. Check what was set up (run as sa)
   --------------------------------------------------------------------- */
SELECT
    pr.name                    AS principal,
    pe.state_desc              AS permission_state,
    pe.permission_name,
    OBJECT_SCHEMA_NAME(pe.major_id) + '.' + OBJECT_NAME(pe.major_id) AS object_name
FROM sys.database_permissions AS pe
JOIN sys.database_principals  AS pr ON pe.grantee_principal_id = pr.principal_id
WHERE pr.name = 'USR_FDE_RO' AND pe.class = 1
ORDER BY object_name, permission_state, permission_name;

SELECT COUNT(*) AS active_fleet_rows FROM FDE_VIEWS.VW_ACTIVE_FLEET;  -- expect 720
GO
