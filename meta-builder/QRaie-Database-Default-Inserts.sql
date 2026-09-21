/*
 *
 *****************************************
 * MUST do GLOBAL Replace in this Script
 *****************************************
 * {...tenantid...} => Actual TenantId
 * {...tenant_name...} => Actual Tenant Name
 * {...email...} => Actual Email-Id of the Tenant
 * {...password...} => Actual Password Has of the Tenant
 * {...TZ...} => Actual TZ of the Tenant
 * {...workplace_QraieBot_api_user_password...}
 * {...workplace_QraieBot_api_uuid_key...}
 * {...workplace_QraieWeb_api_user_password...}
 * {...workplace_QraieWeb_api_uuid_key...}
 *
*/

use {...tenantid...};

SET QUOTED_IDENTIFIER ON;

-- temporarily disable all foreign key constraints in SQL Server,
EXEC sp_MSforeachtable 'ALTER TABLE ? NOCHECK CONSTRAINT ALL';

-- Create User and Tenant without enabling FK
-- Encrypted Password is given by F/E
/* Member Table - Main Admin */
INSERT INTO {...tenantid...}.dbo.[MEMBER] (member_id, username, email, first_name, last_name, password_hash, timezone, is_active, system_role, last_login, created_by, created_at, modified_by, modified_at, reset_password_token, reset_password_expires, email_verified, verification_token, location, tenant_id, management_role, external_user, approval_status, approved_by)
VALUES(N'M100', N'{...tenantid...}', N'{...email...}', N'{...tenantid...}', N'Admin', N'{...password...}', N'{...TZ...}', 1, N'admin', NULL, N'system', getdate(), N'', NULL, NULL, NULL, 0, NULL, N'', N'{...tenantid...}', NULL, 0, N'approved', NULL);

/* Member Table - AsqSAM */
INSERT INTO {...tenantid...}.dbo.[MEMBER] (member_id, username, email, first_name, last_name, password_hash, timezone, is_active, system_role, last_login, created_by, created_at, modified_by, modified_at, reset_password_token, reset_password_expires, email_verified, verification_token, location, tenant_id, management_role, external_user, approval_status, approved_by)
VALUES(N'M101', N'asqSam', N'asqSam@noemail.com', N'Asq', N'Sam', N'$2b$10$GNa1p5orzCnLeoknZKyO4ePivuEx5CwtvlIUifh7O.HmW9MALPkTu', N'UTC', 1, N'erep', NULL, N'system', getdate(), N'', NULL, NULL, NULL, 0, NULL, N'', N'{...tenantid...}', NULL, 0, N'approved', NULL);


INSERT INTO {...tenantid...}.dbo.TENANT (tenant_id, tenant_name, description, status, created_by, created_at, modified_by, modified_at, owner)
VALUES(N'{...tenantid...}', N'{...tenant_name...}', N'{...tenant_name...} Workplace', N'ACTIVE', N'M100', getdate(), NULL, NULL, 1);

-- Setup API user_password, uuid_key for QraieBot
INSERT INTO {...tenantid...}.dbo.system_api_accesstokens (module_id, user_id, user_password, uuid_key, grant_type, access_token, token_validity, token_scope, created_on, status) VALUES(N'Qraie', N'QraieBot', N'{...workplace_QraieBot_api_user_password...}', N'{...workplace_QraieBot_api_uuid_key...}', N'client_credentials', N'', 86400, N'read_write', getdate(), N'A');

-- Setup API user_password, uuid_key for QraieWeb
INSERT INTO {...tenantid...}.dbo.system_api_accesstokens (module_id, user_id, user_password, uuid_key, grant_type, access_token, token_validity, token_scope, created_on, status) VALUES(N'Qraie', N'QraieWeb', N'{...workplace_QraieWeb_api_user_password...}', N'{...workplace_QraieWeb_api_uuid_key...}', N'client_credentials', N'', 86400, N'read_write', getdate(), N'A');


-- MANAGEMENT_ROLES_LOOKUP
INSERT INTO {...tenantid...}.dbo.MANAGEMENT_ROLES_LOOKUP (lead_type_code, lead_type_name, description) VALUES(N'PM', N'Manager', N'Coordinates, organizes and manages the work');
INSERT INTO {...tenantid...}.dbo.MANAGEMENT_ROLES_LOOKUP (lead_type_code, lead_type_name, description) VALUES(N'TM', N'Expert Advisor', N'Provides expertise and guidance on technical stuff');
INSERT INTO {...tenantid...}.dbo.MANAGEMENT_ROLES_LOOKUP (lead_type_code, lead_type_name, description) VALUES(N'SE', N'Solution Planner', N'Designs and plans the approach/solution');
INSERT INTO {...tenantid...}.dbo.MANAGEMENT_ROLES_LOOKUP (lead_type_code, lead_type_name, description) VALUES(N'DEV', N'Work Executor', N'Implements and executes the tasks');


-- ESCALATION_LOOKUP
INSERT INTO {...tenantid...}.dbo.ESCALATION_LOOKUP (escalation_level, member_id, member_name, member_role, is_active, priority, tenant_id, created_by, created_at, modified_by, modified_at)
VALUES(3, N'M100', N'{...tenantid...} Admin', NULL, 1, 1, N'{...tenantid...}', N'M100', getdate(), NULL, NULL);
INSERT INTO {...tenantid...}.dbo.ESCALATION_LOOKUP (escalation_level, member_id, member_name, member_role, is_active, priority, tenant_id, created_by, created_at, modified_by, modified_at)
VALUES(4, N'M100', N'{...tenantid...} Admin', NULL, 1, 1, N'{...tenantid...}', N'M100', getdate(), NULL, NULL);
INSERT INTO {...tenantid...}.dbo.ESCALATION_LOOKUP (escalation_level, member_id, member_name, member_role, is_active, priority, tenant_id, created_by, created_at, modified_by, modified_at)
VALUES(5, N'M100', N'{...tenantid...} Admin', NULL, 1, 1, N'{...tenantid...}', N'M100', getdate(), NULL, NULL);


/*
 *
 * Insert Default Asq Categories and corresponding Issue Type Lookup
 *
*/

-- ISSUE_CATEGORY_LOOKUP
INSERT INTO {...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP (category_code, category_name, description, is_active, sort_order, tenant_id) VALUES(N'TS', N'Support', N'Coordination and Problem-Solving', 1, 1, N'{...tenantid...}');
-- ISSUE_TYPE_LOOKUP
INSERT INTO {...tenantid...}.dbo.ISSUE_TYPE_LOOKUP (issue_type_name, description, category_id) VALUES(N'Issue', N'A problem which impairs or prevents the functions of the product.', IDENT_CURRENT('{...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP'));


-- ISSUE_CATEGORY_LOOKUP
INSERT INTO {...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP (category_code, category_name, description, is_active, sort_order, tenant_id) VALUES(N'DEV', N'Developmment', N'Designing and Improving Services', 1, 2, N'{...tenantid...}');
-- ISSUE_TYPE_LOOKUP
INSERT INTO {...tenantid...}.dbo.ISSUE_TYPE_LOOKUP (issue_type_name, description, category_id) VALUES(N'Requirement', N'A new need or condition to be fulfilled.', IDENT_CURRENT('{...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP'));


-- ISSUE_CATEGORY_LOOKUP
INSERT INTO {...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP (category_code, category_name, description, is_active, sort_order, tenant_id) VALUES(N'DEP', N'Deployment', N'Executing Service Delivery', 1, 0, N'{...tenantid...}');
-- ISSUE_TYPE_LOOKUP
INSERT INTO {...tenantid...}.dbo.ISSUE_TYPE_LOOKUP (issue_type_name, description, category_id) VALUES(N'Tasks', N'Standard Actionable Work items to be Completed.', IDENT_CURRENT('{...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP'));


-- ISSUE_CATEGORY_LOOKUP
INSERT INTO {...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP (category_code, category_name, description, is_active, sort_order, tenant_id) VALUES(N'Other', N'Other', N'other', 1, 3, N'{...tenantid...}');
-- ISSUE_TYPE_LOOKUP
INSERT INTO {...tenantid...}.dbo.ISSUE_TYPE_LOOKUP (issue_type_name, description, category_id) VALUES(N'Information Only', N'For informational purposes only.', IDENT_CURRENT('{...tenantid...}.dbo.ISSUE_CATEGORY_LOOKUP'));


-- MODULE_LOOKUP
INSERT INTO {...tenantid...}.dbo.MODULE_LOOKUP (module_code, module_name, description) VALUES(N'MANAGEMENT', N'Management', N'Framework for planning, executing, and closing projects.');
INSERT INTO {...tenantid...}.dbo.MODULE_LOOKUP (module_code, module_name, description) VALUES(N'QA', N'Quality Assurance', N'Processes to ensure products meet defined standards.');
INSERT INTO {...tenantid...}.dbo.MODULE_LOOKUP (module_code, module_name, description) VALUES(N'SUPPORT', N'Support', N'Procedures for addressing inquiries and issues.');
INSERT INTO {...tenantid...}.dbo.MODULE_LOOKUP (module_code, module_name, description) VALUES(N'TRG_DEV', N'Training & Development', N'Continuous education and skills enhancement for employees..');
INSERT INTO {...tenantid...}.dbo.MODULE_LOOKUP (module_code, module_name, description) VALUES(N'COORDINATION', N'Coordination', N'Processes for planning and executing the movements of Tasks.');
INSERT INTO {...tenantid...}.dbo.MODULE_LOOKUP (module_code, module_name, description) VALUES(N'SAFETY', N'Safety Protocols', N'Ensures compliance with safety regulations and procedures for emergency situations..');
INSERT INTO {...tenantid...}.dbo.MODULE_LOOKUP (module_code, module_name, description) VALUES(N'COMPLIANCE', N'Compliance', N'Processes to ensure adherence to laws and regulations.');
INSERT INTO {...tenantid...}.dbo.MODULE_LOOKUP (module_code, module_name, description) VALUES(N'TRACKING', N'Tracking Systems', N'Methods for monitoring and reporting the status of transport movement.');
INSERT INTO {...tenantid...}.dbo.MODULE_LOOKUP (module_code, module_name, description) VALUES(N'OBOARDING', N'Oboarding', N'Standard procedures for integrating new employees or clients.');


-- PERMISSION_LOOKUP
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'ASK', N'assign_ask', N'Can assign ask tickets to users', 1, N'M100', getdate(), N'{...tenantid...}', N'Y');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'ASK', N'close_ask', N'Can close/resolve ask tickets', 1, N'M100', getdate(), N'{...tenantid...}', N'Y');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'ASK', N'create_ask', N'Can create new ask tickets', 1, N'M100', getdate(), N'{...tenantid...}', N'Y');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'ASK', N'create_ask_attachment', N'can create attachment', 1, N'M100', getdate(), N'{...tenantid...}', N'Y');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'ASK', N'create_client_response', N'Can Respond to Client/Tenant', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'ASK', N'delete_ask', N'Can delete ask tickets', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'ASK', N'delete_ask_attachment', N'can delete attachment', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'ASK', N'edit_ask', N'Can edit ask ticket details', 1, N'M100', getdate(), N'{...tenantid...}', N'Y');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'ASK', N'reopen_ask', N'Can reopen closed ask tickets', 1, N'M100', getdate(), N'{...tenantid...}', N'Y');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'ASK', N'view_ask', N'Can view ask ticket details', 1, N'M100', getdate(), N'{...tenantid...}', N'Y');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'CHAT', N'create_chat_room', N'Can create new chat rooms', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'CHAT', N'delete_chat_message', N'Can delete chat messages', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'CHAT', N'delete_chat_room', N'Can delete chat rooms', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'CHAT', N'manage_chat_room', N'Can manage chat room settings', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'CHAT', N'send_chat_message', N'Can send chat messages', 1, N'M100', getdate(), N'{...tenantid...}', N'Y');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'CHAT', N'view_chat_history', N'Can view chat history', 1, N'M100', getdate(), N'{...tenantid...}', N'Y');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'DELIVERABLE', N'create_deliverable', N'Can create new deliverables', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'DELIVERABLE', N'delete_deliverable', N'Can delete deliverables', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'DELIVERABLE', N'edit_deliverable', N'Can edit deliverable details', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'DELIVERABLE', N'manage_deliverable_assignments', N'Can assign deliverables to users', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'DELIVERABLE', N'manage_deliverable_status', N'Can update deliverable status', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'DELIVERABLE', N'view_deliverable', N'Can view deliverable details', 1, N'M100', getdate(), N'{...tenantid...}', N'Y');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'PROJECT', N'create_project', N'Can create new projects', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'PROJECT', N'delete_project', N'Can delete projects', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'PROJECT', N'edit_project', N'Can edit project details', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'PROJECT', N'manage_project_members', N'Can add/remove project members', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'PROJECT', N'manage_project_settings', N'Can modify project settings', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'PROJECT', N'view_project', N'Can view project details', 1, N'M100', getdate(), N'{...tenantid...}', N'Y');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'ROLE', N'assign_roles', N'Can assign roles to users', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'ROLE', N'create_role', N'Can create new roles', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'ROLE', N'delete_role', N'Can delete roles', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'ROLE', N'edit_role', N'Can edit role details', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'ROLE', N'manage_role_permissions', N'Can modify role permissions', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'ROLE', N'view_roles', N'Can view role list and details', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'SYSTEM', N'manage_backups', N'Can manage system backups', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'SYSTEM', N'manage_system_settings', N'Can modify system settings', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'SYSTEM', N'manage_tenants', N'Can manage tenant configurations', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'SYSTEM', N'view_audit_logs', N'Can view audit logs', 1, N'M100', getdate(), N'{...tenantid...}', N'Y');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'SYSTEM', N'view_system_logs', N'Can view system logs', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'USER', N'create_user', N'Can create new users', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'USER', N'delete_user', N'Can delete users', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'USER', N'edit_user', N'Can edit user details', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'USER', N'manage_user_permissions', N'Can modify user permissions', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'USER', N'manage_user_roles', N'Can assign/remove user roles', 1, N'M100', getdate(), N'{...tenantid...}', N'N');
INSERT INTO {...tenantid...}.dbo.PERMISSION_LOOKUP (category, permission_name, description, is_active, created_by, created_at, tenant_id, permission_def_value) VALUES(N'USER', N'view_users', N'Can view user list and details', 1, N'M100', getdate(), N'{...tenantid...}', N'N');

/* Insert Permission for USER Member */
insert into {...tenantid...}.dbo.MEMBER_USER_PERMISSION
select N'M100', N'{...tenantid...}', permission_id, 1, getdate() from PERMISSION_LOOKUP  ;

/* Create general Channel */
INSERT INTO {...tenantid...}.dbo.CHAT_CHANNELS (channel_name, tenant_id, description, is_private, created_by, created_at, updated_at, target_tenant_id, is_cross_tenant)
VALUES(N'general', N'{...tenantid...}', N'Company-wide announcements and work-based matters', 1, N'M100', getdate(), NULL, NULL, 0);

/* Add Admin to general channel */
INSERT INTO {...tenantid...}.dbo.CHANNEL_MEMBERS (channel_id, member_id, tenant_id, added_by, added_at)
VALUES(IDENT_CURRENT('{...tenantid...}.dbo.CHAT_CHANNELS') , N'M100', N'{...tenantid...}', N'M100', getdate());

/* Create Channel to recieve auto notifications  */
INSERT INTO {...tenantid...}.dbo.CHAT_CHANNELS (channel_name, tenant_id, description, is_private, created_by, created_at, updated_at, target_tenant_id, is_cross_tenant)
VALUES(N'support', N'{...tenantid...}', N'Channel to receive all NEW Asq that gets created for Category=TechSupport', 1, N'M100', getdate(), NULL, NULL, 0);

/* Configure it in CHANNEL_NOTIFS table */
INSERT INTO {...tenantid...}.dbo.CATEGORY_CHANNELS (category_id,channel_id,tenant_id,is_active,created_by,created_at,modified_at )
VALUES(N'1', IDENT_CURRENT('{...tenantid...}.dbo.CHAT_CHANNELS'), N'{...tenantid...}', N'1', N'M100', getdate(), getdate());

-- Re-enable All Foreign Keys:
EXEC sp_MSforeachtable 'ALTER TABLE ? CHECK CONSTRAINT ALL';
