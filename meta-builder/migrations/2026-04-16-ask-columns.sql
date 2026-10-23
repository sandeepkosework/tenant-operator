/*
 * Brings EXISTING tenant databases' ASK table up to QRaie-Table-Script-16April26.sql.
 * Additive and idempotent -- safe to run repeatedly. The only thing removed is the old
 * ASK.status CHECK constraint (it allows just open/in-progress/resolved/closed, so values
 * such as 'cancelled' fail). Run it as a login that can see every tenant database (e.g. sa).
 *
 * @DryRun = 1 (default) only lists the databases it would change; set it to 0 to apply.
 */
SET NOCOUNT ON;
DECLARE @DryRun bit = 1;

DECLARE @db sysname, @sql nvarchar(max), @hasAsk int;
DECLARE c CURSOR LOCAL FAST_FORWARD FOR
    SELECT name FROM sys.databases WHERE database_id > 4 AND state_desc = 'ONLINE';
OPEN c; FETCH NEXT FROM c INTO @db;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @hasAsk = 0;
    SET @sql = N'SELECT @n = COUNT(*) FROM ' + QUOTENAME(@db)
             + N'.sys.tables WHERE name = N''ASK'' AND schema_id = SCHEMA_ID(N''dbo'')';
    EXEC sp_executesql @sql, N'@n int OUTPUT', @n = @hasAsk OUTPUT;

    IF @hasAsk = 1
    BEGIN
        PRINT CASE WHEN @DryRun = 1 THEN N'[dry run] would migrate ' ELSE N'migrating ' END + @db;
        IF @DryRun = 0
        BEGIN
            -- Batch 1: columns and the status constraint.
            SET @sql = N'USE ' + QUOTENAME(@db) + N';
IF COL_LENGTH(N''dbo.ASK'', N''authorized_closer'') IS NULL
    ALTER TABLE dbo.ASK ADD authorized_closer nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL;
IF COL_LENGTH(N''dbo.ASK'', N''issue_category_id'') IS NULL
    ALTER TABLE dbo.ASK ADD issue_category_id int NULL;
IF COL_LENGTH(N''dbo.ASK'', N''priority_rank'') IS NULL
    ALTER TABLE dbo.ASK ADD priority_rank int NULL CONSTRAINT DF_ASK_priority_rank DEFAULT 0;
IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N''dbo.ASK'') AND name = N''status'' AND max_length < 400)
BEGIN
    DECLARE @drop nvarchar(max) = N'''';
    SELECT @drop = @drop + N''ALTER TABLE dbo.ASK DROP CONSTRAINT '' + QUOTENAME(name) + N'';''
        FROM sys.check_constraints
        WHERE parent_object_id = OBJECT_ID(N''dbo.ASK'') AND definition LIKE N''%[[]status]%'';
    EXEC (@drop);
    ALTER TABLE dbo.ASK ALTER COLUMN status nvarchar(200) COLLATE SQL_Latin1_General_CP1_CI_AS NULL;
END';
            EXEC (@sql);

            -- Batch 2: anything that references the new columns, plus the status lookup table.
            SET @sql = N'USE ' + QUOTENAME(@db) + N';
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N''IX_ASK_authorized_closer'' AND object_id = OBJECT_ID(N''dbo.ASK''))
    CREATE NONCLUSTERED INDEX IX_ASK_authorized_closer ON dbo.ASK (authorized_closer ASC) INCLUDE (ask_id, tenant_id, status);
IF OBJECT_ID(N''dbo.ASK_STATUS_LOOKUP'', N''U'') IS NULL
BEGIN
    CREATE TABLE dbo.ASK_STATUS_LOOKUP (
        status_id int IDENTITY(1,1) NOT NULL,
        status_name nvarchar(200) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        description nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        is_default bit NOT NULL DEFAULT 0,
        sort_order int NOT NULL DEFAULT 0,
        category_id nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        is_active bit NOT NULL DEFAULT 1,
        CONSTRAINT PK_ASK_STATUS_LOOKUP PRIMARY KEY (status_id),
        CONSTRAINT UQ_AskStatusLookup_StatusName UNIQUE (status_name)
    );
    INSERT INTO dbo.ASK_STATUS_LOOKUP (status_name, description, is_default, sort_order) VALUES
        (N''open'', N''Newly created ticket'', 1, 1),
        (N''in-progress'', N''Ticket is being worked on'', 0, 2),
        (N''resolved'', N''Ticket has been resolved'', 0, 3),
        (N''closed'', N''Ticket is closed'', 0, 4),
        (N''reopened'', N''Ticket was reopened'', 0, 5);
END';
            EXEC (@sql);
        END
    END
    FETCH NEXT FROM c INTO @db;
END
CLOSE c; DEALLOCATE c;
