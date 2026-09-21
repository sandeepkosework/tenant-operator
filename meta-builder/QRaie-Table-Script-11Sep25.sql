/*
 *
 *****************************************
 * MUST do GLOBAL Replace in this Script
 *****************************************
 * {...tenantid...} => Actual TenantId
 *
*/

-- Create database if it doesn't exist
IF NOT EXISTS (SELECT * FROM sys.databases WHERE name = N'{...tenantid...}')
BEGIN
    CREATE DATABASE [{...tenantid...}]
    COLLATE SQL_Latin1_General_CP1_CI_AS;
END
GO

USE [{...tenantid...}]
GO

SET QUOTED_IDENTIFIER ON;

-- {...tenantid...}.dbo.CHAT_MESSAGES definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.CHAT_MESSAGES;

CREATE TABLE {...tenantid...}.dbo.CHAT_MESSAGES (
        id bigint NOT NULL,
        content nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        [timestamp] datetime NOT NULL,
        fromUserName nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        fromFullName nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        roomName nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        avatar nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        CONSTRAINT PK__CHAT_MES__3213E83FAB2175B3 PRIMARY KEY (id)
);


-- {...tenantid...}.dbo.CHAT_ROOMS definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.CHAT_ROOMS;

CREATE TABLE {...tenantid...}.dbo.CHAT_ROOMS (
        id bigint NOT NULL,
        name nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        admin nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        createdAt datetime DEFAULT getdate() NULL,
        CONSTRAINT PK__CHAT_ROO__3213E83F1D695E6A PRIMARY KEY (id),
        CONSTRAINT UQ__CHAT_ROO__72E12F1B637486F0 UNIQUE (name)
);


-- {...tenantid...}.dbo.CHAT_ROOM_USERS definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.CHAT_ROOM_USERS;

CREATE TABLE {...tenantid...}.dbo.CHAT_ROOM_USERS (
        roomName nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        userName nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        joinedAt datetime DEFAULT getdate() NULL,
        CONSTRAINT PK__CHAT_ROO__2EA1B8B90AF011A1 PRIMARY KEY (roomName,userName)
);


-- {...tenantid...}.dbo.CHAT_USERS definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.CHAT_USERS;

CREATE TABLE {...tenantid...}.dbo.CHAT_USERS (
        id nvarchar(50) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        userName nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        fullName nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        avatar nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        device nvarchar(50) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        lastSeen datetime DEFAULT getdate() NULL,
        CONSTRAINT PK__CHAT_USE__3213E83F4AB89484 PRIMARY KEY (id)
);


-- {...tenantid...}.dbo.DIRECT_MESSAGES definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.DIRECT_MESSAGES;

CREATE TABLE {...tenantid...}.dbo.DIRECT_MESSAGES (
        message_id int IDENTITY(1,1) NOT NULL,
        sender_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        receiver_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        message_text nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        parent_message_id int NULL,
        sent_at datetime DEFAULT getdate() NULL,
        edited_at datetime NULL,
        is_read bit DEFAULT 0 NULL,
        is_deleted bit DEFAULT 0 NULL,
        message_type nvarchar(20) COLLATE SQL_Latin1_General_CP1_CI_AS DEFAULT 'text' NULL,
        is_posted_to_conversation bit DEFAULT 0 NOT NULL,
        CONSTRAINT PK__DIRECT_M__0BBF6EE6A89B348C PRIMARY KEY (message_id)
);
 CREATE NONCLUSTERED INDEX IX_DirectMessages_ConversationOptimized ON dbo.DIRECT_MESSAGES (  tenant_id ASC  , is_deleted ASC  , sent_at DESC  )
         INCLUDE ( is_read , message_id , message_text , message_type , receiver_id , sender_id )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_DirectMessages_ReceiverTenantSentAt ON dbo.DIRECT_MESSAGES (  receiver_id ASC  , tenant_id ASC  , sent_at DESC  )
         INCLUDE ( is_deleted , is_read , message_text , message_type , sender_id )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_DirectMessages_SenderTenantSentAt ON dbo.DIRECT_MESSAGES (  sender_id ASC  , tenant_id ASC  , sent_at DESC  )
         INCLUDE ( is_deleted , is_read , message_text , message_type , receiver_id )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_DirectMessages_UnreadCount ON dbo.DIRECT_MESSAGES (  receiver_id ASC  , tenant_id ASC  , is_read ASC  , is_deleted ASC  )
         WHERE  ([is_read]=(0) AND [is_deleted]=(0))
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_direct_messages_conversation ON dbo.DIRECT_MESSAGES (  sender_id ASC  , receiver_id ASC  , sent_at ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_direct_messages_receiver ON dbo.DIRECT_MESSAGES (  receiver_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_direct_messages_sender ON dbo.DIRECT_MESSAGES (  sender_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_direct_messages_sent_at ON dbo.DIRECT_MESSAGES (  sent_at ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_direct_messages_tenant ON dbo.DIRECT_MESSAGES (  tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
ALTER TABLE {...tenantid...}.dbo.DIRECT_MESSAGES WITH NOCHECK ADD CONSTRAINT CK__DIRECT_ME__messa__491AB698 CHECK (([message_type]='document' OR [message_type]='image' OR [message_type]='file' OR [message_type]='text'));


-- {...tenantid...}.dbo.MANAGEMENT_ROLES_LOOKUP definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.MANAGEMENT_ROLES_LOOKUP;

CREATE TABLE {...tenantid...}.dbo.MANAGEMENT_ROLES_LOOKUP (
        lead_type_id int IDENTITY(1,1) NOT NULL,
        lead_type_code nvarchar(50) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        lead_type_name nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        description nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        CONSTRAINT PK__LEAD_TYP__BF4052DD1FF4FAB6 PRIMARY KEY (lead_type_id),
        CONSTRAINT UQ__LEAD_TYP__8ED6D5EB83BB8B29 UNIQUE (lead_type_code)
);


-- {...tenantid...}.dbo.MODULE_LOOKUP definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.MODULE_LOOKUP;

CREATE TABLE {...tenantid...}.dbo.MODULE_LOOKUP (
        module_id int IDENTITY(1,1) NOT NULL,
        module_code nvarchar(50) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        module_name nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        description nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        CONSTRAINT PK__MODULE_L__1A2D0653934D85B8 PRIMARY KEY (module_id),
        CONSTRAINT UQ__MODULE_L__E5DB09FA36303970 UNIQUE (module_code)
);


-- {...tenantid...}.dbo.NIU_CHAT_MESSAGES definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.NIU_CHAT_MESSAGES;

CREATE TABLE {...tenantid...}.dbo.NIU_CHAT_MESSAGES (
        id bigint NOT NULL,
        content nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        [timestamp] datetime NOT NULL,
        fromUserName nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        fromFullName nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        roomName nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        avatar nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        CONSTRAINT PK__ChatMess__3213E83F004367F6 PRIMARY KEY (id)
);


-- {...tenantid...}.dbo.NIU_CHAT_ROOMS definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.NIU_CHAT_ROOMS;

CREATE TABLE {...tenantid...}.dbo.NIU_CHAT_ROOMS (
        id bigint NOT NULL,
        name nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        admin nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        createdAt datetime DEFAULT getdate() NULL,
        CONSTRAINT PK__ChatRoom__3213E83F261CD8AE PRIMARY KEY (id),
        CONSTRAINT UQ__ChatRoom__72E12F1BECF48FB0 UNIQUE (name)
);


-- {...tenantid...}.dbo.NIU_CHAT_ROOM_USERS definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.NIU_CHAT_ROOM_USERS;

CREATE TABLE {...tenantid...}.dbo.NIU_CHAT_ROOM_USERS (
        roomName nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        userName nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        joinedAt datetime DEFAULT getdate() NULL,
        CONSTRAINT PK__ChatRoom__2EA1B8B961513C74 PRIMARY KEY (roomName,userName)
);


-- {...tenantid...}.dbo.NIU_CHAT_USERS definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.NIU_CHAT_USERS;

CREATE TABLE {...tenantid...}.dbo.NIU_CHAT_USERS (
        id nvarchar(50) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        userName nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        fullName nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        avatar nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        device nvarchar(50) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        lastSeen datetime DEFAULT getdate() NULL,
        CONSTRAINT PK__ChatUser__3213E83FB45C4EB5 PRIMARY KEY (id)
);


-- {...tenantid...}.dbo.system_api_accesstokens definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.system_api_accesstokens;

CREATE TABLE {...tenantid...}.dbo.system_api_accesstokens (
        id int IDENTITY(1,1) NOT NULL,
        module_id nvarchar(32) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        user_id nvarchar(32) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        user_password nvarchar(64) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        uuid_key nvarchar(64) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        grant_type nvarchar(48) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        access_token nvarchar(512) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        token_validity bigint NOT NULL,
        token_scope nvarchar(32) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_on datetime DEFAULT getdate() NULL,
        status nvarchar(2) COLLATE SQL_Latin1_General_CP1_CI_AS DEFAULT 'A' NULL,
        CONSTRAINT PK__system_a__8B92264929027D33 PRIMARY KEY (id,user_id,module_id),
        CONSTRAINT UQ_UserId UNIQUE (user_id)
);


-- {...tenantid...}.dbo.ASK definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.ASK;

CREATE TABLE {...tenantid...}.dbo.ASK (
        ask_id int IDENTITY(1,1) NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        summary nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        reporter nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        escalation_level int NULL,
        module nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        description_detail nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        ETA datetime NULL,
        project_name nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        status nvarchar(20) COLLATE SQL_Latin1_General_CP1_CI_AS DEFAULT 'open' NULL,
        priority nvarchar(20) COLLATE SQL_Latin1_General_CP1_CI_AS DEFAULT 'low' NULL,
        created_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_at datetime DEFAULT getdate() NULL,
        modified_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        modified_at datetime DEFAULT getdate() NULL,
        issue_type_id int NULL,
        CONSTRAINT PK_Ask PRIMARY KEY (ask_id,tenant_id)
);
ALTER TABLE {...tenantid...}.dbo.ASK WITH NOCHECK ADD CONSTRAINT CK__ASK__escalation___67DE6983 CHECK (([escalation_level]>=(0) AND [escalation_level]<=(5)));
ALTER TABLE {...tenantid...}.dbo.ASK WITH NOCHECK ADD CONSTRAINT CK__ASK__status__68D28DBC CHECK (([status]='closed' OR [status]='resolved' OR [status]='in-progress' OR [status]='open'));
ALTER TABLE {...tenantid...}.dbo.ASK WITH NOCHECK ADD CONSTRAINT CK__ASK__priority__6ABAD62E CHECK (([priority]='blocker' OR [priority]='critical' OR [priority]='high' OR [priority]='medium' OR [priority]='low'));


-- {...tenantid...}.dbo.ASK_ATTACHMENT definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.ASK_ATTACHMENT;

CREATE TABLE {...tenantid...}.dbo.ASK_ATTACHMENT (
        attachment_id int IDENTITY(1,1) NOT NULL,
        ask_id int NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        file_name nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        file_path nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        file_type nvarchar(50) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        file_size int NOT NULL,
        file_hash nvarchar(64) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_at datetime DEFAULT getdate() NULL,
        modified_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        modified_at datetime DEFAULT getdate() NULL,
        CONSTRAINT PK__ATTACHME__B74DF4E226D67297 PRIMARY KEY (attachment_id)
);


-- {...tenantid...}.dbo.ASK_COMMENTS definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.ASK_COMMENTS;

CREATE TABLE {...tenantid...}.dbo.ASK_COMMENTS (
        comment_id int IDENTITY(1,1) NOT NULL,
        ask_id int NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        member_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        comment_text nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        comment_type nvarchar(10) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        parent_comment_id int NULL,
        is_deleted bit DEFAULT 0 NULL,
        created_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_at datetime DEFAULT getdate() NULL,
        modified_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        modified_at datetime DEFAULT getdate() NULL,
        CONSTRAINT PK__COMMENT__E79576877172F9BE PRIMARY KEY (comment_id)
);
 CREATE NONCLUSTERED INDEX idx_comment_ask ON dbo.ASK_COMMENTS (  ask_id ASC  , tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_comment_member ON dbo.ASK_COMMENTS (  member_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_comment_type ON dbo.ASK_COMMENTS (  comment_type ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
ALTER TABLE {...tenantid...}.dbo.ASK_COMMENTS WITH NOCHECK ADD CONSTRAINT CK__COMMENT__comment__473C8FC7 CHECK (([comment_type]='EXTERNAL' OR [comment_type]='INTERNAL'));


-- {...tenantid...}.dbo.ASK_COMMENT_ATTACHMENT_STORE definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.ASK_COMMENT_ATTACHMENT_STORE;

CREATE TABLE {...tenantid...}.dbo.ASK_COMMENT_ATTACHMENT_STORE (
        comment_attachment_id int IDENTITY(1,1) NOT NULL,
        comment_id int NOT NULL,
        file_name nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        file_path nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        file_type nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        file_size bigint NOT NULL,
        file_hash nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        is_deleted bit DEFAULT 0 NULL,
        created_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_at datetime DEFAULT getdate() NULL,
        CONSTRAINT PK__COMMENT___9C4867E15665BE6B PRIMARY KEY (comment_attachment_id)
);
 CREATE NONCLUSTERED INDEX idx_comment_attachment_comment ON dbo.ASK_COMMENT_ATTACHMENT_STORE (  comment_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;


-- {...tenantid...}.dbo.ASK_HANDLER definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.ASK_HANDLER;

CREATE TABLE {...tenantid...}.dbo.ASK_HANDLER (
        ask_handler_id int IDENTITY(1,1) NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        ask_id int NOT NULL,
        meta_info_id int NOT NULL,
        created_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_at datetime DEFAULT getdate() NOT NULL,
        modified_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        modified_at datetime DEFAULT getdate() NOT NULL,
        ask_handler nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        ask_owner nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        CONSTRAINT PK__ASK_HAND__50417F73E6B05650 PRIMARY KEY (ask_handler_id),
        CONSTRAINT uk_tenant_ask_meta_info UNIQUE (tenant_id,ask_id,meta_info_id)
);
 CREATE NONCLUSTERED INDEX IX_ASK_HANDLER_ask_owner ON dbo.ASK_HANDLER (  ask_owner ASC  )
         INCLUDE ( ask_handler_id , ask_id )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_ah_ask ON dbo.ASK_HANDLER (  ask_id ASC  , tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_ah_created_by ON dbo.ASK_HANDLER (  created_by ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_ah_meta_info ON dbo.ASK_HANDLER (  meta_info_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_ah_tenant ON dbo.ASK_HANDLER (  tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_ask_handler_member ON dbo.ASK_HANDLER (  ask_handler ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;


-- {...tenantid...}.dbo.CATEGORY_CHANNELS definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.CATEGORY_CHANNELS;

CREATE TABLE {...tenantid...}.dbo.CATEGORY_CHANNELS (
        mapping_id int IDENTITY(1,1) NOT NULL,
        category_id int NOT NULL,
        channel_id int NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        is_active bit DEFAULT 1 NOT NULL,
        created_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_at datetime DEFAULT getdate() NOT NULL,
        modified_at datetime DEFAULT getdate() NOT NULL,
        CONSTRAINT PK__CATEGORY__5AE90045D672CC31 PRIMARY KEY (mapping_id),
        CONSTRAINT UQ_CategoryChannels_CategoryTenant UNIQUE (category_id,tenant_id)
);
 CREATE NONCLUSTERED INDEX idx_category_channels_active ON dbo.CATEGORY_CHANNELS (  is_active ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_category_channels_category ON dbo.CATEGORY_CHANNELS (  category_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_category_channels_channel ON dbo.CATEGORY_CHANNELS (  channel_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_category_channels_tenant ON dbo.CATEGORY_CHANNELS (  tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;


-- {...tenantid...}.dbo.CHANNEL_MEMBERS definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.CHANNEL_MEMBERS;

CREATE TABLE {...tenantid...}.dbo.CHANNEL_MEMBERS (
        channel_id int NOT NULL,
        member_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        added_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        added_at datetime DEFAULT getdate() NULL,
        CONSTRAINT PK_ChannelMembers PRIMARY KEY (channel_id,member_id,tenant_id)
);
 CREATE NONCLUSTERED INDEX IX_CM_member_channel ON dbo.CHANNEL_MEMBERS (  member_id ASC  , channel_id ASC  )
         INCLUDE ( added_at , tenant_id )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_CM_tenant_channel ON dbo.CHANNEL_MEMBERS (  tenant_id ASC  , channel_id ASC  )
         INCLUDE ( added_at , member_id )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_channel_members_channel ON dbo.CHANNEL_MEMBERS (  channel_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_channel_members_member ON dbo.CHANNEL_MEMBERS (  member_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_channel_members_tenant ON dbo.CHANNEL_MEMBERS (  tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;


-- {...tenantid...}.dbo.CHANNEL_MESSAGES definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.CHANNEL_MESSAGES;

CREATE TABLE {...tenantid...}.dbo.CHANNEL_MESSAGES (
        message_id int IDENTITY(1,1) NOT NULL,
        channel_id int NOT NULL,
        sender_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        message_text nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        parent_message_id int NULL,
        sent_at datetime DEFAULT getdate() NULL,
        edited_at datetime NULL,
        is_deleted bit DEFAULT 0 NULL,
        is_posted_to_conversation bit DEFAULT 0 NOT NULL,
        CONSTRAINT PK__CHANNEL___0BBF6EE6F19F0D71 PRIMARY KEY (message_id)
);
 CREATE NONCLUSTERED INDEX idx_channel_messages_channel ON dbo.CHANNEL_MESSAGES (  channel_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_channel_messages_parent ON dbo.CHANNEL_MESSAGES (  parent_message_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_channel_messages_sender ON dbo.CHANNEL_MESSAGES (  sender_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_channel_messages_sent_at ON dbo.CHANNEL_MESSAGES (  sent_at ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_channel_messages_tenant ON dbo.CHANNEL_MESSAGES (  tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_channel_messages_thread ON dbo.CHANNEL_MESSAGES (  channel_id ASC  , parent_message_id ASC  , sent_at ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;


-- {...tenantid...}.dbo.CHANNEL_MESSAGE_ATTACHMENTS definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.CHANNEL_MESSAGE_ATTACHMENTS;

CREATE TABLE {...tenantid...}.dbo.CHANNEL_MESSAGE_ATTACHMENTS (
        attachment_id int IDENTITY(1,1) NOT NULL,
        message_id int NOT NULL,
        file_url nvarchar(500) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        file_type nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        file_name nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        file_size bigint NULL,
        uploaded_at datetime DEFAULT getdate() NULL,
        CONSTRAINT PK__CHANNEL___B74DF4E2E8962925 PRIMARY KEY (attachment_id)
);
 CREATE NONCLUSTERED INDEX idx_channel_attachments_file_type ON dbo.CHANNEL_MESSAGE_ATTACHMENTS (  file_type ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_channel_attachments_message ON dbo.CHANNEL_MESSAGE_ATTACHMENTS (  message_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_channel_attachments_uploaded_at ON dbo.CHANNEL_MESSAGE_ATTACHMENTS (  uploaded_at ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;


-- {...tenantid...}.dbo.CHAT_CHANNELS definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.CHAT_CHANNELS;

CREATE TABLE {...tenantid...}.dbo.CHAT_CHANNELS (
        channel_id int IDENTITY(1,1) NOT NULL,
        channel_name nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        description nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        is_private bit DEFAULT 0 NULL,
        created_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_at datetime DEFAULT getdate() NULL,
        updated_at datetime DEFAULT getdate() NULL,
        target_tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        is_cross_tenant bit DEFAULT 0 NOT NULL,
        is_project_channel bit DEFAULT 0 NOT NULL,
        CONSTRAINT PK__CHAT_CHA__2D0861ABA0D0F6B3 PRIMARY KEY (channel_id),
        CONSTRAINT UQ_ChatChannels_NameTenant UNIQUE (channel_name,tenant_id)
);
 CREATE NONCLUSTERED INDEX IX_CC_created_at ON dbo.CHAT_CHANNELS (  created_at DESC  )
         INCLUDE ( channel_id , channel_name , is_private , tenant_id )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_chat_channels_created_by ON dbo.CHAT_CHANNELS (  created_by ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_chat_channels_project_channel ON dbo.CHAT_CHANNELS (  is_project_channel ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_chat_channels_target_tenant ON dbo.CHAT_CHANNELS (  target_tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_chat_channels_tenant ON dbo.CHAT_CHANNELS (  tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;


-- {...tenantid...}.dbo.CHAT_DIRECT_MESSAGES definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.CHAT_DIRECT_MESSAGES;

CREATE TABLE {...tenantid...}.dbo.CHAT_DIRECT_MESSAGES (
        id bigint NOT NULL,
        sender_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        receiver_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        content nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        [timestamp] datetime DEFAULT getdate() NULL,
        is_read bit DEFAULT 0 NULL,
        CONSTRAINT PK__CHAT_DIR__3213E83F921D6C98 PRIMARY KEY (id)
);


-- {...tenantid...}.dbo.DELIVERABLE definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.DELIVERABLE;

CREATE TABLE {...tenantid...}.dbo.DELIVERABLE (
        deliverable_id int IDENTITY(1,1) NOT NULL,
        project_id int NULL,
        sprint_name nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        start_date datetime NOT NULL,
        end_date datetime NOT NULL,
        status nvarchar(20) COLLATE SQL_Latin1_General_CP1_CI_AS DEFAULT 'Planned' NULL,
        goal nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        created_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_at datetime DEFAULT getdate() NULL,
        modified_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        modified_at datetime DEFAULT getdate() NULL,
        stagingETA datetime NULL,
        liveETA datetime NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        finalStagingETA datetime NULL,
        finalLiveETA datetime NULL,
        types nvarchar(20) COLLATE SQL_Latin1_General_CP1_CI_AS DEFAULT 'development' NOT NULL,
        CONSTRAINT PK__DELIVERA__1A26FA21EFA3B52A PRIMARY KEY (deliverable_id)
);
 CREATE  UNIQUE NONCLUSTERED INDEX UQ_Deliverable_Name_Type ON dbo.DELIVERABLE (  sprint_name ASC  , types ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
ALTER TABLE {...tenantid...}.dbo.DELIVERABLE WITH NOCHECK ADD CONSTRAINT CK__DELIVERAB__types__4AB8E647 CHECK (([types]='milestone' OR [types]='development'));
ALTER TABLE {...tenantid...}.dbo.DELIVERABLE WITH NOCHECK ADD CONSTRAINT CK__DELIVERAB__statu__5A4F643B CHECK (([status]='Completed' OR [status]='Active' OR [status]='Planned'));


-- {...tenantid...}.dbo.DELIVERABLE_ASK_ASSIGNEE definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.DELIVERABLE_ASK_ASSIGNEE;

CREATE TABLE {...tenantid...}.dbo.DELIVERABLE_ASK_ASSIGNEE (
        link_id int IDENTITY(1,1) NOT NULL,
        sprint_id int NOT NULL,
        ask_id int NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        added_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        added_at datetime DEFAULT getdate() NULL,
        CONSTRAINT PK__SPRINT_A__93B0078C13E2C3E3 PRIMARY KEY (link_id),
        CONSTRAINT UQ_SprintAskLink UNIQUE (sprint_id,ask_id)
);


-- {...tenantid...}.dbo.ESCALATION_LOOKUP definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.ESCALATION_LOOKUP;

CREATE TABLE {...tenantid...}.dbo.ESCALATION_LOOKUP (
        lookup_id int IDENTITY(1,1) NOT NULL,
        escalation_level int NOT NULL,
        member_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        member_name nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        member_role nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        is_active bit DEFAULT 1 NOT NULL,
        priority int DEFAULT 1 NOT NULL,
        tenant_id nvarchar(50) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_at datetime DEFAULT getdate() NOT NULL,
        modified_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        modified_at datetime NULL,
        CONSTRAINT PK__ESCALATI__E492CAE463B7CF4A PRIMARY KEY (lookup_id)
);
 CREATE NONCLUSTERED INDEX idx_escalation_lookup_active ON dbo.ESCALATION_LOOKUP (  is_active ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_escalation_lookup_level_tenant ON dbo.ESCALATION_LOOKUP (  escalation_level ASC  , tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_escalation_lookup_member ON dbo.ESCALATION_LOOKUP (  member_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_escalation_lookup_priority ON dbo.ESCALATION_LOOKUP (  escalation_level ASC  , tenant_id ASC  , priority ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
ALTER TABLE {...tenantid...}.dbo.ESCALATION_LOOKUP WITH NOCHECK ADD CONSTRAINT CK__ESCALATIO__escal__0DAFD807 CHECK (([escalation_level]>=(3) AND [escalation_level]<=(5)));


-- {...tenantid...}.dbo.ESCALATION_TRACKING definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.ESCALATION_TRACKING;

CREATE TABLE {...tenantid...}.dbo.ESCALATION_TRACKING (
        escalation_id int IDENTITY(1,1) NOT NULL,
        ask_id int NOT NULL,
        escalated_to_member_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        escalation_level int NOT NULL,
        escalation_reason nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        is_active bit DEFAULT 1 NOT NULL,
        escalated_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        escalated_at datetime DEFAULT getdate() NOT NULL,
        deactivated_at datetime NULL,
        notes nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        tenant_id nvarchar(50) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        modified_at datetime DEFAULT getdate() NOT NULL,
        CONSTRAINT PK__ESCALATI__9E0A567BA8B5F432 PRIMARY KEY (escalation_id)
);
 CREATE NONCLUSTERED INDEX idx_escalation_tracking_active ON dbo.ESCALATION_TRACKING (  is_active ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_escalation_tracking_ask_id ON dbo.ESCALATION_TRACKING (  ask_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_escalation_tracking_escalated_at ON dbo.ESCALATION_TRACKING (  escalated_at ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_escalation_tracking_escalated_to ON dbo.ESCALATION_TRACKING (  escalated_to_member_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_escalation_tracking_level ON dbo.ESCALATION_TRACKING (  escalation_level ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_escalation_tracking_tenant ON dbo.ESCALATION_TRACKING (  tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
ALTER TABLE {...tenantid...}.dbo.ESCALATION_TRACKING WITH NOCHECK ADD CONSTRAINT CK__ESCALATIO__escal__07F6FEB1 CHECK (([escalation_level]>=(1) AND [escalation_level]<=(5)));


-- {...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP;

CREATE TABLE {...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP (
        category_id int IDENTITY(1,1) NOT NULL,
        category_code nvarchar(50) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        category_name nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        description nvarchar(500) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        is_active bit DEFAULT 1 NOT NULL,
        sort_order int DEFAULT 0 NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        CONSTRAINT PK__ISSUE_CA__D54EE9B4AEC7D8E7 PRIMARY KEY (category_id),
        CONSTRAINT UQ_ISSUE_CATEGORY_CODE UNIQUE (category_code)
);
 CREATE NONCLUSTERED INDEX IX_ISSUE_CATEGORY_CODE ON dbo.ISSUE_CATEGORY_LOOKUP (  category_code ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_ISSUE_CATEGORY_IS_ACTIVE ON dbo.ISSUE_CATEGORY_LOOKUP (  is_active ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_ISSUE_CATEGORY_SORT_ORDER ON dbo.ISSUE_CATEGORY_LOOKUP (  sort_order ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_ISSUE_CATEGORY_TENANT_ACTIVE_SORT ON dbo.ISSUE_CATEGORY_LOOKUP (  tenant_id ASC  , is_active ASC  , sort_order ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_ISSUE_CATEGORY_TENANT_ID ON dbo.ISSUE_CATEGORY_LOOKUP (  tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
ALTER TABLE {...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP WITH NOCHECK ADD CONSTRAINT CK_ISSUE_CATEGORY_CODE_LENGTH CHECK ((len([category_code])>=(1) AND len([category_code])<=(50)));
ALTER TABLE {...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP WITH NOCHECK ADD CONSTRAINT CK_ISSUE_CATEGORY_NAME_LENGTH CHECK ((len([category_name])>=(1) AND len([category_name])<=(100)));
ALTER TABLE {...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP WITH NOCHECK ADD CONSTRAINT CK_ISSUE_CATEGORY_DESCRIPTION_LENGTH CHECK ((len([description])<=(500)));
ALTER TABLE {...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP WITH NOCHECK ADD CONSTRAINT CK_ISSUE_CATEGORY_SORT_ORDER CHECK (([sort_order]>=(0)));


-- {...tenantid...}.dbo.ISSUE_TYPE_LOOKUP definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.ISSUE_TYPE_LOOKUP;

CREATE TABLE {...tenantid...}.dbo.ISSUE_TYPE_LOOKUP (
        issue_type_id int IDENTITY(1,1) NOT NULL,
        issue_type_name nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        description nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        category_id int NOT NULL,
        CONSTRAINT PK__ISSUE_TY__4F07869BB122379F PRIMARY KEY (issue_type_id)
);
 CREATE NONCLUSTERED INDEX IX_ISSUE_TYPE_CATEGORY_ID ON dbo.ISSUE_TYPE_LOOKUP (  category_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;


-- {...tenantid...}.dbo.JOB definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.JOB;

CREATE TABLE {...tenantid...}.dbo.JOB (
        job_id int IDENTITY(1,1) NOT NULL,
        user_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        project_id int NOT NULL,
        [role] nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_at datetime DEFAULT getdate() NULL,
        modified_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        modified_at datetime DEFAULT getdate() NULL,
        CONSTRAINT PK__JOB__6E32B6A51ACFEE9E PRIMARY KEY (job_id),
        CONSTRAINT UQ_UserProject UNIQUE (user_id,project_id)
);


-- {...tenantid...}.dbo.[MEMBER] definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.[MEMBER];

CREATE TABLE {...tenantid...}.dbo.[MEMBER] (
        member_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        username nvarchar(50) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        email nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        first_name nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        last_name nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        password_hash nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        timezone nvarchar(50) COLLATE SQL_Latin1_General_CP1_CI_AS DEFAULT 'UTC' NULL,
        is_active bit DEFAULT 1 NULL,
        system_role nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        last_login datetime NULL,
        created_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_at datetime DEFAULT getdate() NULL,
        modified_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        modified_at datetime DEFAULT getdate() NULL,
        reset_password_token nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        reset_password_expires datetime NULL,
        email_verified bit DEFAULT 0 NULL,
        verification_token nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        location nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS DEFAULT 'UTC' NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        management_role nvarchar(50) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        external_user bit DEFAULT 0 NOT NULL,
        approval_status nvarchar(20) COLLATE SQL_Latin1_General_CP1_CI_AS DEFAULT 'pending' NOT NULL,
        approved_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        totp_secret nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS DEFAULT NULL NULL,
        Agent_details nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        CONSTRAINT PK__MEMBER__B29B8534EAD18723 PRIMARY KEY (member_id),
        CONSTRAINT UQ__MEMBER__AB6E6164A3379C31 UNIQUE (email),
        CONSTRAINT UQ__MEMBER__F3DBC57231FF59B9 UNIQUE (username)
);
ALTER TABLE {...tenantid...}.dbo.[MEMBER] WITH NOCHECK ADD CONSTRAINT CK__MEMBER__approval__0169315C CHECK (([approval_status]='rejected' OR [approval_status]='approved' OR [approval_status]='pending'));
ALTER TABLE {...tenantid...}.dbo.[MEMBER] WITH NOCHECK ADD CONSTRAINT CK_MEMBER_EXTERNAL_USER CHECK (([EXTERNAL_USER]=case when upper([tenant_id])=upper('{...tenantid...}') then (0) else (1) end));


-- {...tenantid...}.dbo.MEMBER_USER_PERMISSION definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.MEMBER_USER_PERMISSION;

CREATE TABLE {...tenantid...}.dbo.MEMBER_USER_PERMISSION (
        member_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        permission_id int NOT NULL,
        is_active bit DEFAULT 1 NULL,
        created_at datetime DEFAULT getdate() NULL,
        CONSTRAINT PK_MEMBER_USER_PERMISSION PRIMARY KEY (member_id,tenant_id,permission_id)
);
 CREATE NONCLUSTERED INDEX idx_mup_member_id ON dbo.MEMBER_USER_PERMISSION (  member_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_mup_permission_id ON dbo.MEMBER_USER_PERMISSION (  permission_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_mup_tenant_id ON dbo.MEMBER_USER_PERMISSION (  tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;


-- {...tenantid...}.dbo.MESSAGE_ATTACHMENTS definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.MESSAGE_ATTACHMENTS;

CREATE TABLE {...tenantid...}.dbo.MESSAGE_ATTACHMENTS (
        attachment_id int IDENTITY(1,1) NOT NULL,
        message_id int NULL,
        channel_message_id int NULL,
        file_name nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        file_url nvarchar(500) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        file_type nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        file_size bigint NOT NULL,
        uploaded_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        uploaded_at datetime DEFAULT getdate() NULL,
        is_deleted bit DEFAULT 0 NULL,
        CONSTRAINT PK__MESSAGE___B74DF4E2A83E0ECD PRIMARY KEY (attachment_id)
);
 CREATE NONCLUSTERED INDEX idx_message_attachments_channel_message ON dbo.MESSAGE_ATTACHMENTS (  channel_message_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_message_attachments_message ON dbo.MESSAGE_ATTACHMENTS (  message_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_message_attachments_uploaded_by ON dbo.MESSAGE_ATTACHMENTS (  uploaded_by ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
ALTER TABLE {...tenantid...}.dbo.MESSAGE_ATTACHMENTS WITH NOCHECK ADD CONSTRAINT CHK_MessageAttachments_MessageType CHECK (([message_id] IS NOT NULL AND [channel_message_id] IS NULL OR [message_id] IS NULL AND [channel_message_id] IS NOT NULL));


-- {...tenantid...}.dbo.MESSAGE_READ_STATUS definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.MESSAGE_READ_STATUS;

CREATE TABLE {...tenantid...}.dbo.MESSAGE_READ_STATUS (
        read_status_id int IDENTITY(1,1) NOT NULL,
        message_id int NULL,
        channel_message_id int NULL,
        member_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        is_read bit DEFAULT 0 NULL,
        read_at datetime NULL,
        delivered_at datetime DEFAULT getdate() NULL,
        message_type nvarchar(20) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        CONSTRAINT PK__MESSAGE___568846CA67607208 PRIMARY KEY (read_status_id)
);
 CREATE  UNIQUE NONCLUSTERED INDEX UQ_MessageReadStatus_Channel ON dbo.MESSAGE_READ_STATUS (  channel_message_id ASC  , member_id ASC  )
         WHERE  ([channel_message_id] IS NOT NULL)
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE  UNIQUE NONCLUSTERED INDEX UQ_MessageReadStatus_Direct ON dbo.MESSAGE_READ_STATUS (  message_id ASC  , member_id ASC  )
         WHERE  ([message_id] IS NOT NULL)
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_message_read_status_member ON dbo.MESSAGE_READ_STATUS (  member_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_message_read_status_tenant ON dbo.MESSAGE_READ_STATUS (  tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_message_read_status_unread ON dbo.MESSAGE_READ_STATUS (  member_id ASC  , is_read ASC  , message_type ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
ALTER TABLE {...tenantid...}.dbo.MESSAGE_READ_STATUS WITH NOCHECK ADD CONSTRAINT CK__MESSAGE_R__messa__55808D7D CHECK (([message_type]='channel' OR [message_type]='direct'));
ALTER TABLE {...tenantid...}.dbo.MESSAGE_READ_STATUS WITH NOCHECK ADD CONSTRAINT CHK_MessageReadStatus_MessageType CHECK (([message_id] IS NOT NULL AND [channel_message_id] IS NULL AND [message_type]='direct' OR [message_id] IS NULL AND [channel_message_id] IS NOT NULL AND [message_type]='channel'));


-- {...tenantid...}.dbo.NIU_CHAT_DIRECT_MESSAGES definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.NIU_CHAT_DIRECT_MESSAGES;

CREATE TABLE {...tenantid...}.dbo.NIU_CHAT_DIRECT_MESSAGES (
        id bigint NOT NULL,
        sender_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        receiver_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        content nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        [timestamp] datetime DEFAULT getdate() NULL,
        is_read bit DEFAULT 0 NULL,
        CONSTRAINT PK__DirectMe__3213E83FBCEA7F06 PRIMARY KEY (id)
);


-- {...tenantid...}.dbo.NOTIFICATIONS definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.NOTIFICATIONS;

CREATE TABLE {...tenantid...}.dbo.NOTIFICATIONS (
        notification_id int IDENTITY(1,1) NOT NULL,
        recipient_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        sender_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        message_id int NULL,
        channel_message_id int NULL,
        channel_id int NULL,
        notification_type nvarchar(50) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        title nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        content nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        is_read bit DEFAULT 0 NULL,
        is_delivered bit DEFAULT 0 NULL,
        delivered_at datetime NULL,
        read_at datetime NULL,
        created_at datetime DEFAULT getdate() NULL,
        expires_at datetime NULL,
        metadata nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        CONSTRAINT PK__NOTIFICA__E059842F84B13574 PRIMARY KEY (notification_id)
);
 CREATE NONCLUSTERED INDEX idx_notifications_recipient ON dbo.NOTIFICATIONS (  recipient_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_notifications_sender ON dbo.NOTIFICATIONS (  sender_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_notifications_tenant ON dbo.NOTIFICATIONS (  tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_notifications_type ON dbo.NOTIFICATIONS (  notification_type ASC  , created_at ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_notifications_unread ON dbo.NOTIFICATIONS (  recipient_id ASC  , is_read ASC  , created_at ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
ALTER TABLE {...tenantid...}.dbo.NOTIFICATIONS WITH NOCHECK ADD CONSTRAINT CK__NOTIFICAT__notif__4535171B CHECK (([notification_type]='reaction' OR [notification_type]='reply' OR [notification_type]='mention' OR [notification_type]='channel_message' OR [notification_type]='direct_message'));


-- {...tenantid...}.dbo.PERMISSION_LOOKUP definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.PERMISSION_LOOKUP;

CREATE TABLE {...tenantid...}.dbo.PERMISSION_LOOKUP (
        permission_id int IDENTITY(1,1) NOT NULL,
        category nvarchar(50) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        permission_name nvarchar(50) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        description nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        is_active bit DEFAULT 1 NULL,
        created_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_at datetime DEFAULT getdate() NULL,
        modified_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        modified_at datetime DEFAULT getdate() NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        permission_def_value nvarchar(1) COLLATE SQL_Latin1_General_CP1_CI_AS DEFAULT 'N' NULL,
        CONSTRAINT PK__PERMISSI__E5331AFAE131E4D7 PRIMARY KEY (permission_id)
);


-- {...tenantid...}.dbo.PROJECT definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.PROJECT;

CREATE TABLE {...tenantid...}.dbo.PROJECT (
        project_id int IDENTITY(1,1) NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        project_name nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        project_key nvarchar(10) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        description nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        start_date date NULL,
        end_date date NULL,
        status nvarchar(20) COLLATE SQL_Latin1_General_CP1_CI_AS DEFAULT 'active' NULL,
        project_lead nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        created_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_at datetime DEFAULT getdate() NULL,
        modified_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        modified_at datetime DEFAULT getdate() NULL,
        project_type nvarchar(250) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        BusinessUnit nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS DEFAULT 'Qryde' NOT NULL,
        production bit DEFAULT 0 NOT NULL,
        CONSTRAINT PK__PROJECT__BC799E1F60A8CB2C PRIMARY KEY (project_id),
        CONSTRAINT UQ__PROJECT__30AB21DFDAFE9A61 UNIQUE (project_key)
);
ALTER TABLE {...tenantid...}.dbo.PROJECT WITH NOCHECK ADD CONSTRAINT CK__PROJECT__status__078C1F06 CHECK (([status]='archived' OR [status]='inactive' OR [status]='active'));


-- {...tenantid...}.dbo.PROJECT_CHANNELS definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.PROJECT_CHANNELS;

CREATE TABLE {...tenantid...}.dbo.PROJECT_CHANNELS (
        project_channel_id int IDENTITY(1,1) NOT NULL,
        project_id int NOT NULL,
        channel_id int NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        is_active bit DEFAULT 1 NOT NULL,
        created_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_at datetime DEFAULT getdate() NOT NULL,
        modified_at datetime DEFAULT getdate() NOT NULL,
        CONSTRAINT PK__PROJECT___385792BDCFCE0506 PRIMARY KEY (project_channel_id),
        CONSTRAINT UQ_ProjectChannels_ProjectChannel UNIQUE (project_id,channel_id)
);
 CREATE NONCLUSTERED INDEX IX_PC_channel_active ON dbo.PROJECT_CHANNELS (  channel_id ASC  , is_active ASC  )
         INCLUDE ( project_id , tenant_id )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_project_channels_active ON dbo.PROJECT_CHANNELS (  is_active ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_project_channels_channel ON dbo.PROJECT_CHANNELS (  channel_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_project_channels_project ON dbo.PROJECT_CHANNELS (  project_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_project_channels_tenant ON dbo.PROJECT_CHANNELS (  tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;


-- {...tenantid...}.dbo.PROJECT_META_INFO definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.PROJECT_META_INFO;

CREATE TABLE {...tenantid...}.dbo.PROJECT_META_INFO (
        meta_info_id int IDENTITY(1,1) NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        project_name nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        lead_type_id int NOT NULL,
        module_id int NOT NULL,
        user_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        ext_user_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        created_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_at datetime DEFAULT getdate() NOT NULL,
        modified_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        modified_at datetime DEFAULT getdate() NOT NULL,
        is_active bit DEFAULT 1 NOT NULL,
        CONSTRAINT PK__PROJECT___8EBDEA1156AE1995 PRIMARY KEY (meta_info_id),
        CONSTRAINT uk_tenant_project_lead_module UNIQUE (tenant_id,project_name,lead_type_id,module_id,user_id)
);
 CREATE NONCLUSTERED INDEX IX_PMI_lead_type_active ON dbo.PROJECT_META_INFO (  lead_type_id ASC  , is_active ASC  )
         INCLUDE ( project_name , user_id )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_PMI_project_name ON dbo.PROJECT_META_INFO (  project_name ASC  )
         INCLUDE ( is_active , lead_type_id , meta_info_id , user_id )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_pmi_lead_type ON dbo.PROJECT_META_INFO (  lead_type_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_pmi_module ON dbo.PROJECT_META_INFO (  module_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_pmi_tenant ON dbo.PROJECT_META_INFO (  tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_pmi_user ON dbo.PROJECT_META_INFO (  user_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;


-- {...tenantid...}.dbo.STATUS_LOOKUP definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.STATUS_LOOKUP;

CREATE TABLE {...tenantid...}.dbo.STATUS_LOOKUP (
        template_id int IDENTITY(1,1) NOT NULL,
        template_key nvarchar(50) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        display_text nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        emoji_unicode nvarchar(10) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        default_duration_minutes int NULL,
        is_active bit DEFAULT 1 NOT NULL,
        status_type nvarchar(10) COLLATE SQL_Latin1_General_CP1_CI_AS DEFAULT 'user' NOT NULL,
        sort_order int DEFAULT 0 NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        CONSTRAINT PK__STATUS_L__BE44E07910817108 PRIMARY KEY (template_id),
        CONSTRAINT UQ_STATUS_LOOKUP_TEMPLATE_KEY UNIQUE (template_key)
);
 CREATE NONCLUSTERED INDEX IX_STATUS_LOOKUP_IS_ACTIVE ON dbo.STATUS_LOOKUP (  is_active ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_STATUS_LOOKUP_SORT_ORDER ON dbo.STATUS_LOOKUP (  sort_order ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_STATUS_LOOKUP_STATUS_TYPE ON dbo.STATUS_LOOKUP (  status_type ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_STATUS_LOOKUP_STATUS_TYPE_ACTIVE ON dbo.STATUS_LOOKUP (  status_type ASC  , is_active ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_STATUS_LOOKUP_TENANT_ACTIVE_SORT ON dbo.STATUS_LOOKUP (  tenant_id ASC  , is_active ASC  , sort_order ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_STATUS_LOOKUP_TENANT_ID ON dbo.STATUS_LOOKUP (  tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
ALTER TABLE {...tenantid...}.dbo.STATUS_LOOKUP WITH NOCHECK ADD CONSTRAINT CK_STATUS_LOOKUP_TEMPLATE_KEY_FORMAT CHECK (([template_key] like '[a-z0-9_]%' AND [template_key]=lower([template_key])));
ALTER TABLE {...tenantid...}.dbo.STATUS_LOOKUP WITH NOCHECK ADD CONSTRAINT CK_STATUS_LOOKUP_DISPLAY_TEXT_LENGTH CHECK ((len([display_text])>=(1) AND len([display_text])<=(100)));
ALTER TABLE {...tenantid...}.dbo.STATUS_LOOKUP WITH NOCHECK ADD CONSTRAINT CK_STATUS_LOOKUP_EMOJI_LENGTH CHECK ((len([emoji_unicode])<=(10)));
ALTER TABLE {...tenantid...}.dbo.STATUS_LOOKUP WITH NOCHECK ADD CONSTRAINT CK_STATUS_LOOKUP_DURATION_POSITIVE CHECK (([default_duration_minutes] IS NULL OR [default_duration_minutes]>(0)));
ALTER TABLE {...tenantid...}.dbo.STATUS_LOOKUP WITH NOCHECK ADD CONSTRAINT CK_STATUS_LOOKUP_DURATION_MAX CHECK (([default_duration_minutes] IS NULL OR [default_duration_minutes]<=(43200)));
ALTER TABLE {...tenantid...}.dbo.STATUS_LOOKUP WITH NOCHECK ADD CONSTRAINT CK_STATUS_LOOKUP_STATUS_TYPE CHECK (([status_type]='system' OR [status_type]='user'));


-- {...tenantid...}.dbo.TENANT definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.TENANT;

CREATE TABLE {...tenantid...}.dbo.TENANT (
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        tenant_name nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        description nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        status nvarchar(20) COLLATE SQL_Latin1_General_CP1_CI_AS DEFAULT 'ACTIVE' NULL,
        created_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_at datetime DEFAULT getdate() NULL,
        modified_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        modified_at datetime DEFAULT getdate() NULL,
        owner bit DEFAULT 0 NOT NULL,
        CONSTRAINT PK__TENANT__D6F29F3EFED2AB24 PRIMARY KEY (tenant_id)
);
ALTER TABLE {...tenantid...}.dbo.TENANT WITH NOCHECK ADD CONSTRAINT CK__TENANT__status__7E02B4CC CHECK (([status]='INACTIVE' OR [status]='ACTIVE'));


-- {...tenantid...}.dbo.USER_PREFERENCES definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.USER_PREFERENCES;

CREATE TABLE {...tenantid...}.dbo.USER_PREFERENCES (
        preference_id int IDENTITY(1,1) NOT NULL,
        user_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        send_on_enter bit DEFAULT 1 NOT NULL,
        drafts nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        favourites nvarchar(MAX) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        created_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        created_at datetime DEFAULT getdate() NOT NULL,
        modified_by nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        modified_at datetime DEFAULT getdate() NOT NULL,
        CONSTRAINT PK__USER_PRE__FB41DBCF310D006F PRIMARY KEY (preference_id),
        CONSTRAINT UQ_UserPreferences_User UNIQUE (user_id)
);
 CREATE NONCLUSTERED INDEX idx_user_preferences_created_at ON dbo.USER_PREFERENCES (  created_at ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_user_preferences_tenant_id ON dbo.USER_PREFERENCES (  tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX idx_user_preferences_user_id ON dbo.USER_PREFERENCES (  user_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;


-- {...tenantid...}.dbo.USER_STATUS definition

-- Drop table

-- DROP TABLE {...tenantid...}.dbo.USER_STATUS;

CREATE TABLE {...tenantid...}.dbo.USER_STATUS (
        status_id int IDENTITY(1,1) NOT NULL,
        template_id int NULL,
        member_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        tenant_id nvarchar(255) COLLATE SQL_Latin1_General_CP1_CI_AS NOT NULL,
        status_emoji nvarchar(10) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        status_note nvarchar(100) COLLATE SQL_Latin1_General_CP1_CI_AS NULL,
        is_active bit DEFAULT 1 NOT NULL,
        expires_at datetime2 NULL,
        created_at datetime2 DEFAULT getdate() NOT NULL,
        updated_at datetime2 DEFAULT getdate() NOT NULL,
        CONSTRAINT PK__USER_STA__3683B531ED9E40DA PRIMARY KEY (status_id),
        CONSTRAINT UQ_USER_STATUS_MEMBER UNIQUE (member_id)
);
 CREATE NONCLUSTERED INDEX IX_USER_STATUS_CREATED_AT ON dbo.USER_STATUS (  created_at ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_USER_STATUS_EXPIRES_AT ON dbo.USER_STATUS (  expires_at ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_USER_STATUS_IS_ACTIVE ON dbo.USER_STATUS (  is_active ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_USER_STATUS_TEMPLATE_ID ON dbo.USER_STATUS (  template_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_USER_STATUS_TENANT_ACTIVE ON dbo.USER_STATUS (  tenant_id ASC  , is_active ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
 CREATE NONCLUSTERED INDEX IX_USER_STATUS_TENANT_ID ON dbo.USER_STATUS (  tenant_id ASC  )
         WITH (  PAD_INDEX = OFF ,FILLFACTOR = 100  ,SORT_IN_TEMPDB = OFF , IGNORE_DUP_KEY = OFF , STATISTICS_NORECOMPUTE = OFF , ONLINE = OFF , ALLOW_ROW_LOCKS = ON , ALLOW_PAGE_LOCKS = ON  )
         ON [PRIMARY ] ;
ALTER TABLE {...tenantid...}.dbo.USER_STATUS WITH NOCHECK ADD CONSTRAINT CK_USER_STATUS_NOTE_LENGTH CHECK ((len([status_note])<=(100)));
ALTER TABLE {...tenantid...}.dbo.USER_STATUS WITH NOCHECK ADD CONSTRAINT CK_USER_STATUS_EMOJI_LENGTH CHECK ((len([status_emoji])<=(10)));


-- {...tenantid...}.dbo.ASK foreign keys

ALTER TABLE {...tenantid...}.dbo.ASK ADD CONSTRAINT FK_Ask_CreatedBy FOREIGN KEY (created_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.ASK ADD CONSTRAINT FK_Ask_IssueType FOREIGN KEY (issue_type_id) REFERENCES {...tenantid...}.dbo.ISSUE_TYPE_LOOKUP(issue_type_id);
ALTER TABLE {...tenantid...}.dbo.ASK ADD CONSTRAINT FK_Ask_ModifiedBy FOREIGN KEY (modified_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.ASK ADD CONSTRAINT FK_Ask_Reporter FOREIGN KEY (reporter) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.ASK ADD CONSTRAINT FK_Ask_Tenant FOREIGN KEY (tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id);


-- {...tenantid...}.dbo.ASK_ATTACHMENT foreign keys

ALTER TABLE {...tenantid...}.dbo.ASK_ATTACHMENT ADD CONSTRAINT FK_Attachment_Ask FOREIGN KEY (ask_id,tenant_id) REFERENCES {...tenantid...}.dbo.ASK(ask_id,tenant_id) ON DELETE CASCADE;
ALTER TABLE {...tenantid...}.dbo.ASK_ATTACHMENT ADD CONSTRAINT FK_Attachment_CreatedBy FOREIGN KEY (created_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.ASK_ATTACHMENT ADD CONSTRAINT FK_Attachment_ModifiedBy FOREIGN KEY (modified_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);


-- {...tenantid...}.dbo.ASK_COMMENTS foreign keys

ALTER TABLE {...tenantid...}.dbo.ASK_COMMENTS ADD CONSTRAINT FK_Comment_Ask FOREIGN KEY (ask_id,tenant_id) REFERENCES {...tenantid...}.dbo.ASK(ask_id,tenant_id) ON DELETE CASCADE;
ALTER TABLE {...tenantid...}.dbo.ASK_COMMENTS ADD CONSTRAINT FK_Comment_CreatedBy FOREIGN KEY (created_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.ASK_COMMENTS ADD CONSTRAINT FK_Comment_Member FOREIGN KEY (member_id) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.ASK_COMMENTS ADD CONSTRAINT FK_Comment_ModifiedBy FOREIGN KEY (modified_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.ASK_COMMENTS ADD CONSTRAINT FK_Comment_ParentComment FOREIGN KEY (parent_comment_id) REFERENCES {...tenantid...}.dbo.ASK_COMMENTS(comment_id);


-- {...tenantid...}.dbo.ASK_COMMENT_ATTACHMENT_STORE foreign keys

ALTER TABLE {...tenantid...}.dbo.ASK_COMMENT_ATTACHMENT_STORE ADD CONSTRAINT FK_CommentAttachment_Comment FOREIGN KEY (comment_id) REFERENCES {...tenantid...}.dbo.ASK_COMMENTS(comment_id) ON DELETE CASCADE;
ALTER TABLE {...tenantid...}.dbo.ASK_COMMENT_ATTACHMENT_STORE ADD CONSTRAINT FK_CommentAttachment_CreatedBy FOREIGN KEY (created_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);


-- {...tenantid...}.dbo.ASK_HANDLER foreign keys

ALTER TABLE {...tenantid...}.dbo.ASK_HANDLER ADD CONSTRAINT fk_ah_ask FOREIGN KEY (ask_id,tenant_id) REFERENCES {...tenantid...}.dbo.ASK(ask_id,tenant_id) ON DELETE CASCADE;
ALTER TABLE {...tenantid...}.dbo.ASK_HANDLER ADD CONSTRAINT fk_ah_created_by FOREIGN KEY (created_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.ASK_HANDLER ADD CONSTRAINT fk_ah_meta_info FOREIGN KEY (meta_info_id) REFERENCES {...tenantid...}.dbo.PROJECT_META_INFO(meta_info_id);
ALTER TABLE {...tenantid...}.dbo.ASK_HANDLER ADD CONSTRAINT fk_ah_modified_by FOREIGN KEY (modified_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.ASK_HANDLER ADD CONSTRAINT fk_ah_tenant FOREIGN KEY (tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id);
ALTER TABLE {...tenantid...}.dbo.ASK_HANDLER ADD CONSTRAINT fk_ask_handler_member FOREIGN KEY (ask_handler) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.ASK_HANDLER ADD CONSTRAINT fk_ask_owner_member FOREIGN KEY (ask_owner) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);


-- {...tenantid...}.dbo.CATEGORY_CHANNELS foreign keys

ALTER TABLE {...tenantid...}.dbo.CATEGORY_CHANNELS ADD CONSTRAINT FK_CategoryChannels_Category FOREIGN KEY (category_id) REFERENCES {...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP(category_id) ON DELETE CASCADE;
ALTER TABLE {...tenantid...}.dbo.CATEGORY_CHANNELS ADD CONSTRAINT FK_CategoryChannels_Channel FOREIGN KEY (channel_id) REFERENCES {...tenantid...}.dbo.CHAT_CHANNELS(channel_id) ON DELETE CASCADE;
ALTER TABLE {...tenantid...}.dbo.CATEGORY_CHANNELS ADD CONSTRAINT FK_CategoryChannels_CreatedBy FOREIGN KEY (created_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.CATEGORY_CHANNELS ADD CONSTRAINT FK_CategoryChannels_Tenant FOREIGN KEY (tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id);


-- {...tenantid...}.dbo.CHANNEL_MEMBERS foreign keys

ALTER TABLE {...tenantid...}.dbo.CHANNEL_MEMBERS ADD CONSTRAINT FK_ChannelMembers_AddedBy FOREIGN KEY (added_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.CHANNEL_MEMBERS ADD CONSTRAINT FK_ChannelMembers_Channel FOREIGN KEY (channel_id) REFERENCES {...tenantid...}.dbo.CHAT_CHANNELS(channel_id) ON DELETE CASCADE;
ALTER TABLE {...tenantid...}.dbo.CHANNEL_MEMBERS ADD CONSTRAINT FK_ChannelMembers_Member FOREIGN KEY (member_id) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.CHANNEL_MEMBERS ADD CONSTRAINT FK_ChannelMembers_Tenant FOREIGN KEY (tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id);


-- {...tenantid...}.dbo.CHANNEL_MESSAGES foreign keys

ALTER TABLE {...tenantid...}.dbo.CHANNEL_MESSAGES ADD CONSTRAINT FK_ChannelMessages_Channel FOREIGN KEY (channel_id) REFERENCES {...tenantid...}.dbo.CHAT_CHANNELS(channel_id) ON DELETE CASCADE;
ALTER TABLE {...tenantid...}.dbo.CHANNEL_MESSAGES ADD CONSTRAINT FK_ChannelMessages_Parent FOREIGN KEY (parent_message_id) REFERENCES {...tenantid...}.dbo.CHANNEL_MESSAGES(message_id);
ALTER TABLE {...tenantid...}.dbo.CHANNEL_MESSAGES ADD CONSTRAINT FK_ChannelMessages_Sender FOREIGN KEY (sender_id) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.CHANNEL_MESSAGES ADD CONSTRAINT FK_ChannelMessages_Tenant FOREIGN KEY (tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id);


-- {...tenantid...}.dbo.CHANNEL_MESSAGE_ATTACHMENTS foreign keys

ALTER TABLE {...tenantid...}.dbo.CHANNEL_MESSAGE_ATTACHMENTS ADD CONSTRAINT FK_ChannelAttachments_Message FOREIGN KEY (message_id) REFERENCES {...tenantid...}.dbo.CHANNEL_MESSAGES(message_id) ON DELETE CASCADE;


-- {...tenantid...}.dbo.CHAT_CHANNELS foreign keys

ALTER TABLE {...tenantid...}.dbo.CHAT_CHANNELS ADD CONSTRAINT FK_ChatChannels_CreatedBy FOREIGN KEY (created_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.CHAT_CHANNELS ADD CONSTRAINT FK_ChatChannels_TargetTenant FOREIGN KEY (target_tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id);
ALTER TABLE {...tenantid...}.dbo.CHAT_CHANNELS ADD CONSTRAINT FK_ChatChannels_Tenant FOREIGN KEY (tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id);


-- {...tenantid...}.dbo.CHAT_DIRECT_MESSAGES foreign keys

ALTER TABLE {...tenantid...}.dbo.CHAT_DIRECT_MESSAGES ADD CONSTRAINT FK_ChatDirectMessages_Receiver FOREIGN KEY (receiver_id) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.CHAT_DIRECT_MESSAGES ADD CONSTRAINT FK_ChatDirectMessages_Sender FOREIGN KEY (sender_id) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);


-- {...tenantid...}.dbo.DELIVERABLE foreign keys

ALTER TABLE {...tenantid...}.dbo.DELIVERABLE ADD CONSTRAINT FK_Deliverable_CreatedBy FOREIGN KEY (created_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.DELIVERABLE ADD CONSTRAINT FK_Deliverable_ModifiedBy FOREIGN KEY (modified_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.DELIVERABLE ADD CONSTRAINT FK_Deliverable_Project FOREIGN KEY (project_id) REFERENCES {...tenantid...}.dbo.PROJECT(project_id) ON DELETE CASCADE;
ALTER TABLE {...tenantid...}.dbo.DELIVERABLE ADD CONSTRAINT FK_Deliverable_Tenant FOREIGN KEY (tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id);


-- {...tenantid...}.dbo.DELIVERABLE_ASK_ASSIGNEE foreign keys

ALTER TABLE {...tenantid...}.dbo.DELIVERABLE_ASK_ASSIGNEE ADD CONSTRAINT FK_SprintAskLink_Ask FOREIGN KEY (ask_id,tenant_id) REFERENCES {...tenantid...}.dbo.ASK(ask_id,tenant_id) ON DELETE CASCADE;
ALTER TABLE {...tenantid...}.dbo.DELIVERABLE_ASK_ASSIGNEE ADD CONSTRAINT FK_SprintAskLink_Member FOREIGN KEY (added_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.DELIVERABLE_ASK_ASSIGNEE ADD CONSTRAINT FK_SprintAskLink_Sprint FOREIGN KEY (sprint_id) REFERENCES {...tenantid...}.dbo.DELIVERABLE(deliverable_id) ON DELETE CASCADE;


-- {...tenantid...}.dbo.ESCALATION_LOOKUP foreign keys

ALTER TABLE {...tenantid...}.dbo.ESCALATION_LOOKUP ADD CONSTRAINT FK_EscalationLookup_CreatedBy FOREIGN KEY (created_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.ESCALATION_LOOKUP ADD CONSTRAINT FK_EscalationLookup_Member FOREIGN KEY (member_id) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.ESCALATION_LOOKUP ADD CONSTRAINT FK_EscalationLookup_ModifiedBy FOREIGN KEY (modified_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);


-- {...tenantid...}.dbo.ESCALATION_TRACKING foreign keys

ALTER TABLE {...tenantid...}.dbo.ESCALATION_TRACKING ADD CONSTRAINT FK_EscalationTracking_EscalatedBy FOREIGN KEY (escalated_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.ESCALATION_TRACKING ADD CONSTRAINT FK_EscalationTracking_EscalatedTo FOREIGN KEY (escalated_to_member_id) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);


-- {...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP foreign keys

ALTER TABLE {...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP ADD CONSTRAINT FK_ISSUE_CATEGORY_TENANT FOREIGN KEY (tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id) ON DELETE CASCADE;


-- {...tenantid...}.dbo.ISSUE_TYPE_LOOKUP foreign keys

ALTER TABLE {...tenantid...}.dbo.ISSUE_TYPE_LOOKUP ADD CONSTRAINT FK_IssueType_Category FOREIGN KEY (category_id) REFERENCES {...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP(category_id);


-- {...tenantid...}.dbo.JOB foreign keys

ALTER TABLE {...tenantid...}.dbo.JOB ADD CONSTRAINT FK_Job_CreatedBy FOREIGN KEY (created_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.JOB ADD CONSTRAINT FK_Job_ModifiedBy FOREIGN KEY (modified_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.JOB ADD CONSTRAINT FK_Job_Project FOREIGN KEY (project_id) REFERENCES {...tenantid...}.dbo.PROJECT(project_id) ON DELETE CASCADE;
ALTER TABLE {...tenantid...}.dbo.JOB ADD CONSTRAINT FK_Job_User FOREIGN KEY (user_id) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id) ON DELETE CASCADE;


-- {...tenantid...}.dbo.[MEMBER] foreign keys

ALTER TABLE {...tenantid...}.dbo.[MEMBER] ADD CONSTRAINT FK_Member_ApprovedBy FOREIGN KEY (approved_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.[MEMBER] ADD CONSTRAINT FK_Member_ManagementRole FOREIGN KEY (management_role) REFERENCES {...tenantid...}.dbo.MANAGEMENT_ROLES_LOOKUP(lead_type_code);
ALTER TABLE {...tenantid...}.dbo.[MEMBER] ADD CONSTRAINT FK_Member_Tenant FOREIGN KEY (tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id);


-- {...tenantid...}.dbo.MEMBER_USER_PERMISSION foreign keys

ALTER TABLE {...tenantid...}.dbo.MEMBER_USER_PERMISSION ADD CONSTRAINT FK_MUP_Member FOREIGN KEY (member_id) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.MEMBER_USER_PERMISSION ADD CONSTRAINT FK_MUP_Permission FOREIGN KEY (permission_id) REFERENCES {...tenantid...}.dbo.PERMISSION_LOOKUP(permission_id);
ALTER TABLE {...tenantid...}.dbo.MEMBER_USER_PERMISSION ADD CONSTRAINT FK_MUP_Tenant FOREIGN KEY (tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id);


-- {...tenantid...}.dbo.MESSAGE_ATTACHMENTS foreign keys

ALTER TABLE {...tenantid...}.dbo.MESSAGE_ATTACHMENTS ADD CONSTRAINT FK_MessageAttachments_ChannelMessage FOREIGN KEY (channel_message_id) REFERENCES {...tenantid...}.dbo.CHANNEL_MESSAGES(message_id) ON DELETE CASCADE;
ALTER TABLE {...tenantid...}.dbo.MESSAGE_ATTACHMENTS ADD CONSTRAINT FK_MessageAttachments_DirectMessage FOREIGN KEY (message_id) REFERENCES {...tenantid...}.dbo.DIRECT_MESSAGES(message_id) ON DELETE CASCADE;
ALTER TABLE {...tenantid...}.dbo.MESSAGE_ATTACHMENTS ADD CONSTRAINT FK_MessageAttachments_UploadedBy FOREIGN KEY (uploaded_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);


-- {...tenantid...}.dbo.MESSAGE_READ_STATUS foreign keys

ALTER TABLE {...tenantid...}.dbo.MESSAGE_READ_STATUS ADD CONSTRAINT FK_MessageReadStatus_ChannelMessage FOREIGN KEY (channel_message_id) REFERENCES {...tenantid...}.dbo.CHANNEL_MESSAGES(message_id) ON DELETE CASCADE;
ALTER TABLE {...tenantid...}.dbo.MESSAGE_READ_STATUS ADD CONSTRAINT FK_MessageReadStatus_DirectMessage FOREIGN KEY (message_id) REFERENCES {...tenantid...}.dbo.DIRECT_MESSAGES(message_id) ON DELETE CASCADE;
ALTER TABLE {...tenantid...}.dbo.MESSAGE_READ_STATUS ADD CONSTRAINT FK_MessageReadStatus_Member FOREIGN KEY (member_id) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.MESSAGE_READ_STATUS ADD CONSTRAINT FK_MessageReadStatus_Tenant FOREIGN KEY (tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id);


-- {...tenantid...}.dbo.NIU_CHAT_DIRECT_MESSAGES foreign keys

ALTER TABLE {...tenantid...}.dbo.NIU_CHAT_DIRECT_MESSAGES ADD CONSTRAINT FK_DirectMessages_Receiver FOREIGN KEY (receiver_id) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.NIU_CHAT_DIRECT_MESSAGES ADD CONSTRAINT FK_DirectMessages_Sender FOREIGN KEY (sender_id) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);


-- {...tenantid...}.dbo.NOTIFICATIONS foreign keys

ALTER TABLE {...tenantid...}.dbo.NOTIFICATIONS ADD CONSTRAINT FK_Notifications_ChannelMessage FOREIGN KEY (channel_message_id) REFERENCES {...tenantid...}.dbo.CHANNEL_MESSAGES(message_id) ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE {...tenantid...}.dbo.NOTIFICATIONS ADD CONSTRAINT FK_Notifications_DirectMessage FOREIGN KEY (message_id) REFERENCES {...tenantid...}.dbo.DIRECT_MESSAGES(message_id) ON DELETE CASCADE ON UPDATE CASCADE;


-- {...tenantid...}.dbo.PERMISSION_LOOKUP foreign keys

ALTER TABLE {...tenantid...}.dbo.PERMISSION_LOOKUP ADD CONSTRAINT FK_PermissionLookup_CreatedBy FOREIGN KEY (created_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.PERMISSION_LOOKUP ADD CONSTRAINT FK_PermissionLookup_ModifiedBy FOREIGN KEY (modified_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.PERMISSION_LOOKUP ADD CONSTRAINT FK_PermissionLookup_Tenant FOREIGN KEY (tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id);


-- {...tenantid...}.dbo.PROJECT foreign keys

ALTER TABLE {...tenantid...}.dbo.PROJECT ADD CONSTRAINT FK_Project_CreatedBy FOREIGN KEY (created_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.PROJECT ADD CONSTRAINT FK_Project_Lead FOREIGN KEY (project_lead) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.PROJECT ADD CONSTRAINT FK_Project_ModifiedBy FOREIGN KEY (modified_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.PROJECT ADD CONSTRAINT FK_Project_Tenant FOREIGN KEY (tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id);


-- {...tenantid...}.dbo.PROJECT_CHANNELS foreign keys

ALTER TABLE {...tenantid...}.dbo.PROJECT_CHANNELS ADD CONSTRAINT FK_ProjectChannels_Channel FOREIGN KEY (channel_id) REFERENCES {...tenantid...}.dbo.CHAT_CHANNELS(channel_id) ON DELETE CASCADE;
ALTER TABLE {...tenantid...}.dbo.PROJECT_CHANNELS ADD CONSTRAINT FK_ProjectChannels_CreatedBy FOREIGN KEY (created_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.PROJECT_CHANNELS ADD CONSTRAINT FK_ProjectChannels_Project FOREIGN KEY (project_id) REFERENCES {...tenantid...}.dbo.PROJECT(project_id) ON DELETE CASCADE;
ALTER TABLE {...tenantid...}.dbo.PROJECT_CHANNELS ADD CONSTRAINT FK_ProjectChannels_Tenant FOREIGN KEY (tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id);


-- {...tenantid...}.dbo.PROJECT_META_INFO foreign keys

ALTER TABLE {...tenantid...}.dbo.PROJECT_META_INFO ADD CONSTRAINT fk_pmi_created_by FOREIGN KEY (created_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.PROJECT_META_INFO ADD CONSTRAINT fk_pmi_lead_type FOREIGN KEY (lead_type_id) REFERENCES {...tenantid...}.dbo.MANAGEMENT_ROLES_LOOKUP(lead_type_id);
ALTER TABLE {...tenantid...}.dbo.PROJECT_META_INFO ADD CONSTRAINT fk_pmi_modified_by FOREIGN KEY (modified_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.PROJECT_META_INFO ADD CONSTRAINT fk_pmi_module FOREIGN KEY (module_id) REFERENCES {...tenantid...}.dbo.MODULE_LOOKUP(module_id);
ALTER TABLE {...tenantid...}.dbo.PROJECT_META_INFO ADD CONSTRAINT fk_pmi_tenant FOREIGN KEY (tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id);
ALTER TABLE {...tenantid...}.dbo.PROJECT_META_INFO ADD CONSTRAINT fk_pmi_user FOREIGN KEY (user_id) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);


-- {...tenantid...}.dbo.STATUS_LOOKUP foreign keys

ALTER TABLE {...tenantid...}.dbo.STATUS_LOOKUP ADD CONSTRAINT FK_STATUS_LOOKUP_TENANT FOREIGN KEY (tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id) ON DELETE CASCADE;


-- {...tenantid...}.dbo.TENANT foreign keys

ALTER TABLE {...tenantid...}.dbo.TENANT ADD CONSTRAINT FK_Tenant_CreatedBy FOREIGN KEY (created_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.TENANT ADD CONSTRAINT FK_Tenant_ModifiedBy FOREIGN KEY (modified_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);


-- {...tenantid...}.dbo.USER_PREFERENCES foreign keys

ALTER TABLE {...tenantid...}.dbo.USER_PREFERENCES ADD CONSTRAINT FK_UserPreferences_CreatedBy FOREIGN KEY (created_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.USER_PREFERENCES ADD CONSTRAINT FK_UserPreferences_ModifiedBy FOREIGN KEY (modified_by) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);
ALTER TABLE {...tenantid...}.dbo.USER_PREFERENCES ADD CONSTRAINT FK_UserPreferences_Tenant FOREIGN KEY (tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id);
ALTER TABLE {...tenantid...}.dbo.USER_PREFERENCES ADD CONSTRAINT FK_UserPreferences_User FOREIGN KEY (user_id) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id);


-- {...tenantid...}.dbo.USER_STATUS foreign keys

ALTER TABLE {...tenantid...}.dbo.USER_STATUS ADD CONSTRAINT FK_USER_STATUS_MEMBER FOREIGN KEY (member_id) REFERENCES {...tenantid...}.dbo.[MEMBER](member_id) ON DELETE CASCADE;
ALTER TABLE {...tenantid...}.dbo.USER_STATUS ADD CONSTRAINT FK_USER_STATUS_STATUS_LOOKUP FOREIGN KEY (template_id) REFERENCES {...tenantid...}.dbo.STATUS_LOOKUP(template_id);
ALTER TABLE {...tenantid...}.dbo.USER_STATUS ADD CONSTRAINT FK_USER_STATUS_TENANT FOREIGN KEY (tenant_id) REFERENCES {...tenantid...}.dbo.TENANT(tenant_id) ON DELETE CASCADE;