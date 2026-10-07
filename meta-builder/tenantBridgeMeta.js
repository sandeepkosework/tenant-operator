import mongoose from "mongoose";
import fs from "fs";
import path from "path";
import { v4 as uuidv4 } from "uuid";
import sql from "mssql";
import dotenv from "dotenv";
import { fileURLToPath } from "url";
import bcrypt from "bcrypt";
import crypto from "crypto";
import {
    BridgeSchema,
    AuthSchema,
    SequenceSchema,
    UserSchema,
    WfmPayCodeSchema,
    WfmDepartmentSchema,
    WfmActivityTypeSchema,
    EmployeeSchema,
    ActorSchema,
    IoTDeviceSchema,
    IoTEventLookupSchema,
    IoTEventSchema
} from "./bridge-schema-definition.js";
import { getActorsTemplate } from "./actor.template.js";

dotenv.config();


const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
let tenantConn = null;
let connectedTenant = null;


async function getTenantConn(tenantId) {
    if (
        tenantConn &&
        connectedTenant === tenantId &&
        tenantConn.readyState === 1
    ) {
        return tenantConn;
    }

    const dbName = `${tenantId}-bridge`;
    // directConnection=true: the shared Mongo is a replica set that advertises
    // its members by compose hostnames (mongo1/mongo2/mongo3) which don't
    // resolve from a pod, so discovery would fail. Override with
    // MONGO_URI_OPTIONS if the target Mongo needs different options.
    const mongoOptions = process.env.MONGO_URI_OPTIONS || "authSource=admin&directConnection=true";
    const uri = `${process.env.QRAIEAI_MONGODB_URI}${dbName}?${mongoOptions}`;

    tenantConn = mongoose.createConnection(uri, {
        maxPoolSize: 10,
        serverSelectionTimeoutMS: 30000,
        connectTimeoutMS: 30000
    });

    // 🔥 CRITICAL: wait for connection
    await new Promise((resolve, reject) => {
        tenantConn.once("connected", resolve);
        tenantConn.once("error", reject);
    });

    connectedTenant = tenantId;
    console.log(`✅ Connected to ${dbName}`);

    tenantConn.on("disconnected", () => {
        console.warn(`⚠️ Mongo disconnected: ${dbName}`);
        tenantConn = null;
        connectedTenant = null;
    });

    return tenantConn;
}

function deepReplace(obj, replacements) {
    if (typeof obj === "string") {
        let result = obj;
        for (const [key, value] of Object.entries(replacements)) {
            result = result.replaceAll(key, value);
        }
        return result;
    }

    if (Array.isArray(obj)) {
        return obj.map(item => deepReplace(item, replacements));
    }

    if (obj && typeof obj === "object") {
        const out = {};
        for (const k of Object.keys(obj)) {
            out[k] = deepReplace(obj[k], replacements);
        }
        return out;
    }

    return obj;
}

function applyTemplate(obj, variables) {
    if (Array.isArray(obj)) {
        return obj.map(item => applyTemplate(item, variables));
    }

    if (obj !== null && typeof obj === "object") {
        const result = {};
        for (const key of Object.keys(obj)) {
            result[key] = applyTemplate(obj[key], variables);
        }
        return result;
    }

    if (typeof obj === "string") {
        return obj.replace(/{{(.*?)}}/g, function (_, key) {
            const value = variables[key.trim()];
            return value !== undefined && value !== null ? value : "";
        });
    }

    return obj;
}
// Vault access is NOT done from this script -- tenant-operator (the
// Python caller) already talks to Vault for everything else it does, so
// it resolves every value this job needs and hands them over as plain
// env vars instead of this job needing its own `vault` binary + token.
// Kept the same fail-hard-on-missing-value behavior as the old
// safeVault() (shelling out to `vault kv get`) had, just sourced from
// process.env instead.
const requireEnv = (name) => {
    const val = process.env[name];
    if (!val) {
        throw new Error(`Missing required env var: ${name}`);
    }
    return val;
};
// Fixed, chart-defined service ports (see helm-chart-bridge/values.yaml) --
// every tenant's own namespace runs these same services on these same
// ports, so unlike the old docker-compose setup (one shared host, dynamic
// per-tenant ports tracked in ports.json) there's nothing per-tenant to
// look up here at all.
const BRIDGE_PORT = 30170;              // service "bridge" (image acetaxi-bridge)
const MICROSERVICE_QRAIE_PORT = 80;     // service "microservice-qraie"
const WFM_MICROSERVICE_PORT = 9001;     // service "wfm-microservice"
const QRAIE_REDIS_SHARED_PORT = 6379;   // service "qraie-redis-shared"

async function bridgeMetaBuilderService(conn, tenantId, tenantName, domain, email, isStaging) {
    try {
        /* ------------------ Validate inputs ------------------ */
        if (!tenantId) throw new Error("tenantId is required");
        if (!conn) throw new Error("Mongo connection is required");

        // /* ------------------ Values resolved by tenant-operator, passed as env ------------------ */
        const redisPassword = requireEnv("REDIS_PASSWORD");
        const redishost = requireEnv("REDIS_HOST");

        const dbHost = requireEnv("DB_HOST");
        const dbPort = requireEnv("DB_PORT");
        const dbUser = requireEnv("DB_USER");
        const dbPass = requireEnv("DB_PASSWORD");
        // The real SQL Server database/login name meta_builder_job.py already
        // created (tenant.slug, e.g. "bridge-meta-test-16") -- NOT tenantId
        // (the bare tenant_name used for Mongo/domain purposes below). Using
        // tenantId here would connect with a `database:`/schema that doesn't
        // exist, which SQL Server reports as a login failure rather than a
        // "database not found" error.
        const dbName = requireEnv("DB_NAME");

        const mongoHost = requireEnv("MONGO_DB_HOST");
        const mongoPort = requireEnv("MONGO_DB_PORT");
        const mongoUser = requireEnv("MONGO_DB_USERNAME");
        const mongoPassword = requireEnv("MONGO_DB_PASSWORD");
        const jwtSecret = requireEnv("JWT_SECRETKEY");
        const rtSecret = requireEnv("RT_SECRETKEY");
        const dlmSecretKey = requireEnv("DLM_SECRET_KEY");
        const slmKbAuthPassword = requireEnv("SLM_KB_AUTH_PASSWORD");

        // /* ------------------ Load template ------------------ */
        let template;
        try {
            const templatePath = path.resolve(
                __dirname,
                "metaBridgeInfo_template.json"
            );

            if (!fs.existsSync(templatePath)) {
                throw new Error(`Template not found at ${templatePath}`);
            }

            template = JSON.parse(fs.readFileSync(templatePath, "utf8"));
        } catch (err) {
            throw new Error(
                `metaBridgeInfo_template.json not found or invalid: ${err.message}`
            );
        }

        /* ------------------ Build data ------------------ */
        const data = {
            TENANT_ID: tenantId,
            TENANT_ID_UPPER: tenantId.toUpperCase(),
            DISPLAY_NAME: tenantName,
            TENANT_EMAIL: email,
            DOMAIN_NAME: domain,
            STG_ENV: isStaging ? "stg" : "",
            PHONE_SYSTEM_ENV: isStaging ? "STG" : "PROD",
            // Optional third-party API token for the tenant's services. Not
            // committed: supply it as the PERPLEXITY_TOKEN env var on the Job.
            PERPLEXITY_TOKEN: process.env.PERPLEXITY_TOKEN || "",

            NEXT_PORT: BRIDGE_PORT,
            MICROSERVICE_PORT: MICROSERVICE_QRAIE_PORT,
            WFM_MICROSERVICE_PORT: WFM_MICROSERVICE_PORT,

            REDIS_IP: redishost,
            REDIS_PORT: QRAIE_REDIS_SHARED_PORT,
            REDIS_PASSWORD: redisPassword,

            DB_HOST: dbHost,
            DB_PORT: dbPort,
            DB_USER: dbUser,
            DB_PASSWORD: dbPass,
            DB_NAME: dbName,

            MONGO_DB_HOST: mongoHost,
            MONGO_DB_PORT: mongoPort,
            MONGO_DB_USERNAME: mongoUser,
            MONGO_DB_PASSWORD: mongoPassword,

            JWT_SECRETKEY: jwtSecret,
            RT_SECRETKEY: rtSecret,
            DLM_SECRET_KEY: dlmSecretKey,
            SLM_KB_AUTH_PASSWORD: slmKbAuthPassword,

            DATE_NOW: new Date().toISOString(),
            TODAY_DATE: new Date().toISOString().slice(0, 10)
        };


        /* ------------------ Apply template ------------------ */
        const finalJson = applyTemplate(template, data);

        /* ------------------ Mongo upsert ------------------ */
        const BridgeMetaInfo = conn.model("bridgeMetaInfo", BridgeSchema);

        await BridgeMetaInfo.updateOne(
            { "tenantObj.tenantId": tenantId.toUpperCase() },
            {
                $set: {
                    ...finalJson,
                    updatedAt: new Date(),
                    createdAt: new Date()
                },
            },
            { upsert: true }
        );

        return data;

    } catch (err) {
        console.error(`❌ bridgeMetaBuilderService failed for ${tenantId}`, err.message);

        // Optional: return structured error
        throw {
            service: "bridgeMetaBuilderService",
            tenantId,
            error: err.message
        };
    }
}

async function ensureDefaultSequences(conn) {
    const Sequence = conn.model("wfm_sequences", SequenceSchema);

    const defaultSeq = [
        { sequenceCode: "UR", sequenceName: "users", sequenceNumber: 500 },
        { sequenceCode: "E", sequenceName: "employees", sequenceNumber: 500 },
        { sequenceCode: "PC", sequenceName: "paycodes", sequenceNumber: 500 }
    ];

    for (const seq of defaultSeq) {
        await Sequence.updateOne(
            { sequenceCode: seq.sequenceCode, sequenceName: seq.sequenceName },
            { $setOnInsert: seq },
            { upsert: true }
        );
    }
}

async function getNext(conn, code) {
    const Sequence = conn.model("wfm_sequences", SequenceSchema);
    const seq = await Sequence.findOneAndUpdate(
        { sequenceCode: code },
        { $inc: { sequenceNumber: 1 } },
        { returnDocument: "after" }
    );

    return `${code}${seq.sequenceNumber}`;
}

async function seedAclData(conn, tenantId) {
    const RelationMapSchema = new mongoose.Schema({
        relation:     { type: String, required: true, unique: true },
        capabilities: { type: [String], default: [] },
        description:  { type: String, default: '' },
        color:        { type: String, default: '#6B7280' },
        priority:     { type: Number, default: 50 },
        role:         { type: String, default: null },
        propagates:   { type: Boolean, default: false },
        isSystem:     { type: Boolean, default: false },
        updatedBy:    { type: String, default: 'system' },
    }, { collection: 'acl_relation_maps' });

    const NamespaceSchema = new mongoose.Schema({
        prefix:     { type: String, required: true, unique: true },
        label:      { type: String, required: true },
        description:{ type: String, default: '' },
        color:      { type: String, default: '#6B7280' },
        kind:       { type: String, enum: ['subject', 'resource', 'any'], default: 'resource' },
        isBoundary: { type: Boolean, default: false },
        isSystem:   { type: Boolean, default: false },
    }, { collection: 'acl_namespaces' });

    const TripleSchema = new mongoose.Schema({
        subject:   { type: String, required: true },
        relation:  { type: String, required: true },
        object:    { type: String, required: true },
        notes:     { type: String, default: '' },
        createdBy: { type: String, default: 'system' },
    }, { collection: 'acl_triples' });
    TripleSchema.index({ subject: 1, relation: 1, object: 1 }, { unique: true });

    const RelationMap = conn.model('acl_relation_maps', RelationMapSchema);
    const Namespace   = conn.model('acl_namespaces',    NamespaceSchema);
    const Triple      = conn.model('acl_triples',       TripleSchema);

    await Promise.all([
        RelationMap.deleteMany({}),
        Namespace.deleteMany({}),
        Triple.deleteMany({}),
    ]);

    await RelationMap.insertMany([
        { relation: 'member_of', capabilities: [], description: 'Group membership � subject is a member of the object group',              color: '#34D399', priority: 0,  role: 'membership', propagates: false, isSystem: true,  updatedBy: 'system' },
        { relation: 'contains',  capabilities: [], description: 'Resource hierarchy � object contains the subject resource',               color: '#F87171', priority: 0,  role: 'hierarchy',  propagates: true,  isSystem: true,  updatedBy: 'system' },
        { relation: 'viewer',    capabilities: ['read'],                                       description: 'Read-only access to the resource',                            color: '#7A8099', priority: 80, role: null,         propagates: false, isSystem: false, updatedBy: 'system' },
        { relation: 'executor',  capabilities: ['read', 'execute'],                            description: 'Can read and run/invoke the resource',                        color: '#A78BFA', priority: 60, role: null,         propagates: false, isSystem: false, updatedBy: 'system' },
        { relation: 'editor',    capabilities: ['read', 'execute', 'edit', 'add_kb', 'create'],description: 'Can read, run, edit and add knowledge base documents',        color: '#2DD4BF', priority: 40, role: null,         propagates: false, isSystem: false, updatedBy: 'admin'  },
        { relation: 'manager',   capabilities: ['read', 'create', 'edit', 'disable', 'execute'],description: 'Can manage eReps and ActionServer servers � no purge',       color: '#F0A500', priority: 30, role: null,         propagates: false, isSystem: false, updatedBy: 'admin'  },
        { relation: 'owner',     capabilities: ['read', 'execute', 'edit', 'add_kb', 'manage_acl', 'create', 'disable', 'purge'], description: 'Full control including deletion and permission management', color: '#F0A500', priority: 10, role: null, propagates: false, isSystem: false, updatedBy: 'admin' },
    ]);

    await Namespace.insertMany([
        { prefix: 'user:',         label: 'User',           description: 'Individual human user account',                                               color: '#2DD4BF', kind: 'subject',   isBoundary: false, isSystem: true  },
        { prefix: 'group:',        label: 'Group',          description: 'Collection of users � grants apply to all members via member_of',             color: '#34D399', kind: 'subject',   isBoundary: false, isSystem: true  },
        { prefix: 'agent:',        label: 'Agent',          description: 'AI agent � can be subject (orchestrator) or object (target) of a triple',     color: '#A78BFA', kind: 'any',       isBoundary: false, isSystem: true  },
        { prefix: 'tool:',         label: 'Tool',           description: 'Callable tool or API an agent can invoke',                                    color: '#F0A500', kind: 'resource',  isBoundary: false, isSystem: true  },
        { prefix: 'module:',       label: 'Module',         description: 'Top-level application module � acts as a resource container',                 color: '#F87171', kind: 'resource',  isBoundary: false, isSystem: false },
        { prefix: 'feature:',      label: 'Feature',        description: 'A specific feature or capability within a module',                            color: '#60A5FA', kind: 'resource',  isBoundary: false, isSystem: false },
        { prefix: 'actionserver:', label: 'ActionServer',   description: 'ActionServer server managed by Galaxy',                                       color: '#E879F9', kind: 'resource',  isBoundary: false, isSystem: false },
        { prefix: 'erep:',         label: 'eRep',           description: 'AI representative � security boundary: tool access is managed independently', color: '#A78BFA', kind: 'any',       isBoundary: true,  isSystem: false },
        { prefix: 'kb:',           label: 'Knowledge Base', description: 'Document store or knowledge base attached to an agent',                       color: '#E879F9', kind: 'resource',  isBoundary: false, isSystem: false },
        { prefix: 'project:',      label: 'Project',        description: 'Resource container � grants on a project cascade to contained resources',     color: '#F87171', kind: 'resource',  isBoundary: false, isSystem: false },
        { prefix: 'workspace:',    label: 'Workspace',      description: 'Multi-step agent workflow or pipeline',                                       color: '#044e0d', kind: 'resource',  isBoundary: false, isSystem: false },
    ]);

    const triples = [
        // module:galaxy ? features
        { subject: 'module:galaxy',       relation: 'contains', object: 'feature:galaxy.create-erep',         notes: '' },
        { subject: 'module:galaxy',       relation: 'contains', object: 'feature:galaxy.update-erep',         notes: '' },
        { subject: 'module:galaxy',       relation: 'contains', object: 'feature:galaxy.disable-erep',        notes: '' },
        { subject: 'module:galaxy',       relation: 'contains', object: 'feature:galaxy.create-actionserver', notes: '' },
        { subject: 'module:galaxy',       relation: 'contains', object: 'feature:galaxy.update-actionserver', notes: '' },
        { subject: 'module:galaxy',       relation: 'contains', object: 'feature:galaxy.delete-actionserver', notes: '' },
        // module:controlops ? features
        { subject: 'module:controlops',   relation: 'contains', object: 'feature:controlops.create-workspace', notes: '' },
        { subject: 'module:controlops',   relation: 'contains', object: 'feature:controlops.update-workspace', notes: '' },
        { subject: 'module:controlops',   relation: 'contains', object: 'feature:controlops.run-workspace',    notes: '' },
        { subject: 'module:controlops',   relation: 'contains', object: 'feature:controlops.delete-workspace', notes: '' },
        // module:organization ? features
        { subject: 'module:organization', relation: 'contains', object: 'feature:organization.create-employee',    notes: '' },
        { subject: 'module:organization', relation: 'contains', object: 'feature:organization.update-employee',    notes: '' },
        { subject: 'module:organization', relation: 'contains', object: 'feature:organization.create-department',  notes: '' },
        { subject: 'module:organization', relation: 'contains', object: 'feature:organization.delete-department',  notes: '' },
        { subject: 'module:organization', relation: 'contains', object: 'feature:organization.create-workorder',   notes: '' },
        { subject: 'module:organization', relation: 'contains', object: 'feature:organization.update-workorder',   notes: '' },
        { subject: 'module:organization', relation: 'contains', object: 'feature:organization.dispatch-workorder', notes: '' },
        // Admin group owns all modules
        { subject: 'group:admins', relation: 'owner', object: 'module:galaxy',        notes: 'Full access to Galaxy' },
        { subject: 'group:admins', relation: 'owner', object: 'module:controlops',    notes: 'Full access to CtrlOps' },
        { subject: 'group:admins', relation: 'owner', object: 'module:organization',  notes: 'Full access to Organization' },
        { subject: 'group:admins', relation: 'owner', object: 'module:workplace',     notes: 'Full access to Workplace' },
        // Role-based group grants
        { subject: 'group:galaxy-managers',       relation: 'manager', object: 'module:galaxy',       notes: '' },
        { subject: 'group:galaxy-viewers',        relation: 'viewer',  object: 'module:galaxy',       notes: '' },
        { subject: 'group:controlops-editors',    relation: 'editor',  object: 'module:controlops',   notes: '' },
        { subject: 'group:controlops-managers',   relation: 'manager', object: 'module:controlops',   notes: '' },
        { subject: 'group:controlops-viewers',    relation: 'viewer',  object: 'module:controlops',   notes: '' },
        { subject: 'group:organization-managers', relation: 'manager', object: 'module:organization', notes: '' },
        { subject: 'group:organization-viewers',  relation: 'viewer',  object: 'module:organization', notes: '' },
        // Tenant admin day-one access
        { subject: `user:${tenantId}`, relation: 'member_of', object: 'group:admins', notes: 'Tenant admin bootstrapped on setup' },
    ];

    for (const t of triples) {
        try {
            await Triple.create({ ...t, createdBy: 'system' });
        } catch (err) {
            if (err.code !== 11000) console.warn('ACL triple skip:', err.message);
        }
    }

    console.log(`? ACL seed complete for tenant ${tenantId}`);
}

async function tenantDatabaseInitializer(conn, tenantId, tenantName, password, email, domain, qraieBotPassword, isStaging, sqlConfig = {}) {
    const dbName = `${tenantId}-bridge`;

    const AuthColl = conn.model("wfm_auth", AuthSchema);

    const Users = conn.model("wfm_users", UserSchema);

    const createdCollections = [];

    try {
        // ============ 1) CREATE SEQUENCES =============
        await ensureDefaultSequences(conn);
        createdCollections.push("wfm_sequences");

        // ============ 2) CREATE ADMIN USER =============

        const userId = await getNext(conn, "UR");

        const update = {
            $set: {
                userId,
                userName: tenantId,
                emailId: email,
                fullName: tenantName,
                enabled2FA: false,
                loginCount: { allowed: 0, failed: 0 },
                phoneNumbers: []
            },
            $setOnInsert: {
                password: await bcrypt.hash(password, 10),
                createdOn: Date.now()
            }
        };

        await Users.updateOne({ userId }, update, { upsert: true });

        createdCollections.push("wfm_users");

        // ============ 3) AUTH CREATION =============

        // const AuthColl = conn.model("auth", Auth.schema);
        await AuthColl.updateOne(
            { username: tenantId, tenantId },
            {
                $set: {
                    referenceId: userId,
                    tenantId,
                    username: tenantId,
                    roles: [
                        {
                            permissions: [
                                { roleId: "userManagement", hasAccess: true },
                                { roleId: "addNewActivityType", hasAccess: true },
                                { roleId: "CalculatePay", hasAccess: true },
                                { roleId: "unLockData", hasAccess: true },
                                { roleId: "generatePayRollFile", hasAccess: true },
                                { roleId: "LockData", hasAccess: true }
                            ]
                        }
                    ]
                },
                $setOnInsert: {
                    password: await bcrypt.hash(password, 10),
                    salt: ""
                }
            },
            { upsert: true }
        );

        createdCollections.push("wfm_auth");

        // ============ 4) CREATE WFM LOOKUP + BASE COLLECTIONS =============
        await wfmLookupSeeder(conn, tenantId, userId);
        createdCollections.push("wfm_departments", "wfm_employees", "wfm_paycodes", "wfm_activitytypes");
        // ============ 5) CREATE CONTROL OPS DEFAULT ACTORS  =============
        await insertDefaultActors(
            conn,
            tenantId,
            domain,
            qraieBotPassword,
            isStaging
        );
        // ============ 6) CREATE IOT LOOKUPS  =============
        await iotLookupSeeder(conn, tenantId);
        createdCollections.push('iot_eventLookup');
        // ============ 7) CREATE IOT DEVICES  =============
        await iotDeviceSeeder(conn, tenantId);
        createdCollections.push('iot_devices');




        createdCollections.push("controlops_actors");
        // ============ 8) SEED ACL DATA =============
        await seedAclData(conn, tenantId);
        createdCollections.push("acl_relation_maps", "acl_namespaces", "acl_triples");
        // ============ 9) SEED IOT SECURITY CONFIG =============
        await configSeeder(conn, tenantId);
        createdCollections.push("iot_security");
        // ============ 10) SEED CONTROLOPS DATA CONNECTION REGISTRY =============
        await dataConnectionSeeder(conn, tenantId, sqlConfig);
        createdCollections.push("controlops_database_registry");
        // ============ 11) CREATE IOT EVENTS COLLECTION =============
        // iot_events has a model but no default rows -- create it explicitly
        // so the collection exists as soon as the tenant is onboarded.
        await conn.model("iot_events", IoTEventSchema).createCollection();
        createdCollections.push("iot_events");
        // ============ 12) RETURN SUMMARY =============
        return {
            success: true,
            db: dbName,
            createdCollections
        };

    } catch (err) {
        console.error("Tenant initialization failed:", err);
        throw err;

    } finally {
        await conn.close();
    }
}
async function insertDefaultActors(conn, tenantId, domain, qraieBotPassword, isStaging) {
    const ActorModel = conn.model(
        "controlops_actors",
        ActorSchema
    );

    const replacements = {
        "{{TENANT_ID}}": tenantId,
        "{{DOMAIN_NAME}}": domain,
        "{{STG_ENV}}": isStaging ? "stg" : "",
        "{{QRAIBE_BOT_PASSWORD}}": qraieBotPassword
    };

    const actors = getActorsTemplate().map(actor => {
        const resolved = deepReplace(actor, replacements);

        return {
            ...resolved,
            enabled: true,
            createdAt: new Date(),
            updatedAt: new Date()
        };
    });

    for (const actor of actors) {
        await ActorModel.updateOne(
            { actor_id: actor.actor_id },
            { $set: actor },
            { upsert: true }
        );
    }

    console.log("✅ controlops_actors inserted");
}
async function wfmLookupSeeder(conn, tenantId, adminUserId) {

    const WfmPayCode = conn.model("wfm_paycodes", WfmPayCodeSchema);
    const WfmDepartment = conn.model("wfm_departments", WfmDepartmentSchema);
    const WfmActivityType = conn.model("wfm_activitytypes", WfmActivityTypeSchema);
    const Employee = conn.model("wfm_employees", EmployeeSchema);

    // 2�⃣ Insert base lookups
    const activitytypes = [
        {
            "name": "Paid Absences",
            "description": "Absence activity type",
            "publicId": "A500",
            "startDate": "2024-07-11T18:30:00.000Z",
            "endDate": "2030-07-31T18:30:00.000Z",
            "enabled": true,
            "color": "#4bf7e6",
            "employeeTypeGroup": "Technicians",
            "clockInOutRequired": true,
            "payCode": "PC553",
            "autoAccept": true,
            "subTypes": [
                { "name": "Vacations", "color": "#d97f2a", "enabled": true, "isTime": false, "hours": "0800", "subTypePayCode": "PC581", "countedTowardsOverTime": false },
                { "name": "Sick Leave", "color": "#710c06", "enabled": true, "isTime": false, "hours": "0800", "subTypePayCode": "PC579", "countedTowardsOverTime": true },
                { "name": "Personal Holiday", "color": "#ff7b00", "enabled": true, "isTime": false, "hours": "0800", "subTypePayCode": "PC549", "countedTowardsOverTime": true },
                { "name": "Bereavement", "color": "#FFFFFF", "enabled": true, "isTime": false, "hours": "0800", "subTypePayCode": "PC570", "countedTowardsOverTime": true },
                { "name": "Jury Duty", "color": "#FFFFFF", "enabled": true, "isTime": false, "hours": "0800", "subTypePayCode": "PC572", "countedTowardsOverTime": true },
                { "name": "Military Leave Paid", "color": "#ffffff", "enabled": true, "isTime": false, "hours": "0800", "subTypePayCode": "PC575", "countedTowardsOverTime": false },
                { "name": "Admin Other", "color": "#8800ff", "enabled": true, "isTime": true, "subTypePayCode": "PC553", "countedTowardsOverTime": true },
                { "name": "FMLA Paid", "color": "#c71c1c", "enabled": true, "isTime": false, "hours": "0800", "subTypePayCode": "PC547", "countedTowardsOverTime": false },
                { "name": "FMLA Vacation", "color": "#8fe98f", "enabled": true, "isTime": false, "hours": "0800", "subTypePayCode": "PC548", "countedTowardsOverTime": false }
            ]
        },
        {
            "name": "Segment",
            "description": "Segment",
            "publicId": "A524",
            "startDate": "2024-11-30T18:30:00.000Z",
            "endDate": "2033-01-30T18:30:00.000Z",
            "enabled": true,
            "color": "#d3e99d",
            "employeeTypeGroup": "Managers",
            "clockInOutRequired": true,
            "payCode": "PC553",
            "autoAccept": true,
            "subTypes": [
                { "name": "Straight", "color": "#2e2cba", "enabled": true, "isTime": true, "subTypePayCode": "PC553", "countedTowardsOverTime": true },
                { "name": "Split", "color": "#0eb9e8", "enabled": true, "isTime": true, "subTypePayCode": "PC553", "countedTowardsOverTime": true }
            ]
        },
        {
            "name": "Special",
            "description": "Special",
            "publicId": "A525",
            "startDate": "2024-11-30T18:30:00.000Z",
            "endDate": "2051-01-30T18:30:00.000Z",
            "enabled": true,
            "color": "#b61e1e",
            "employeeTypeGroup": "Dispatcher",
            "clockInOutRequired": true,
            "payCode": "PC553",
            "autoAccept": true,
            "subTypes": [
                { "name": "Bike Rally", "color": "#ffffff", "enabled": true, "isTime": false, "hours": "0800", "subTypePayCode": "PC553", "countedTowardsOverTime": true },
                { "name": "Sea Witch", "color": "#FFFFFF", "enabled": true, "isTime": false, "hours": "0800", "subTypePayCode": "PC553", "countedTowardsOverTime": true },
                { "name": "Stuff the Bus", "color": "#aeb04c", "enabled": true, "isTime": false, "hours": "0800", "subTypePayCode": "PC553", "countedTowardsOverTime": true }
            ]
        },
        {
            "name": "Safety",
            "description": "Safety",
            "publicId": "A567",
            "startDate": "2025-03-13T03:00:00.000Z",
            "endDate": "2025-03-31T03:00:00.000Z",
            "enabled": true,
            "color": "#00ffae",
            "employeeTypeGroup": "DTC",
            "clockInOutRequired": true,
            "payCode": "PC553",
            "autoAccept": true,
            "subTypes": [
                { "name": "Safety Meeting", "enabled": true, "isTime": false, "hours": "0200", "subTypePayCode": "PC553", "countedTowardsOverTime": true },
                { "name": "ARC Meeting", "color": "#FFFFFF", "enabled": true, "isTime": false, "hours": "0200", "subTypePayCode": "PC553", "countedTowardsOverTime": true }
            ]
        }
    ]
    for (const activity of activitytypes) {
        await WfmActivityType.updateOne(
            { publicId: activity.publicId },
            {
                $set: {
                    ...activity,
                    updatedOn: new Date()
                },
                $setOnInsert: {
                    createdOn: new Date()
                }
            },
            { upsert: true }
        );
    }
    // 3�⃣ Insert base paycode
    const paycodes = [
        {
            "publicId": "PC547",
            "name": "FMLA Sick",
            "description": "FMLA Sick",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 0,
            "enabled": true,
            "internalPayCode": "FMLAS",
            "CountedTowardsOverTime": "2"
        },
        {
            "publicId": "PC548",
            "name": "FMLA Vacation",
            "description": "FMLA Vacation",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 105,
            "enabled": true,
            "internalPayCode": "FMLAV",
            "CountedTowardsOverTime": "2"
        },
        {
            "publicId": "PC549",
            "name": "Personal Holiday",
            "description": "Personal Holiday",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 101,
            "enabled": true,
            "internalPayCode": "HOLP",
            "CountedTowardsOverTime": "0"
        },
        {
            "publicId": "PC550",
            "name": "DART Holidays",
            "description": "DART Holidays",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 106,
            "enabled": true,
            "internalPayCode": "HOLS",
            "CountedTowardsOverTime": "0"
        },
        {
            "publicId": "PC553",
            "name": "Regular Pay",
            "description": "Regular Pay",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 12,
            "enabled": true,
            "internalPayCode": "REG",
            "CountedTowardsOverTime": "0"
        },
        {
            "publicId": "PC562",
            "name": "FMLA Pending",
            "description": "FMLA Pending",
            "payrollCodes": [
                {
                    "publicId": "PRC617",
                    "name": "NP",
                    "description": "Non-Payroll",
                    "enabled": true
                }
            ],
            "payRate": 100,
            "enabled": true,
            "internalPayCode": "FMLAX",
            "CountedTowardsOverTime": false
        },
        {
            "publicId": "PC563",
            "name": "Un-Paid Absence",
            "description": "Un-Paid Absence",
            "payrollCodes": [
                {
                    "publicId": "PRC617",
                    "name": "NP",
                    "description": "Non-Payroll",
                    "enabled": true
                }
            ],
            "payRate": 1,
            "enabled": true,
            "internalPayCode": "NOPAY",
            "CountedTowardsOverTime": false
        },
        {
            "publicId": "PC564",
            "name": "OT-Overtime for Holidays",
            "description": "OT-Overtime for Holidays",
            "payrollCodes": [
                {
                    "publicId": "PRC618",
                    "name": "OTH",
                    "description": "OT-Overtime for Holidays",
                    "enabled": true
                }
            ],
            "payRate": 101,
            "enabled": true,
            "internalPayCode": "OTH",
            "CountedTowardsOverTime": "0"
        },
        {
            "publicId": "PC565",
            "name": "Overtime Weekly",
            "description": "Overtime Weekly",
            "payrollCodes": [
                {
                    "publicId": "PRC615",
                    "name": "OTW",
                    "description": "Overtime - Weekly",
                    "enabled": true
                }
            ],
            "payRate": 101,
            "enabled": true,
            "internalPayCode": "OTW",
            "CountedTowardsOverTime": false
        },
        {
            "publicId": "PC566",
            "name": "Paid Emergency Leave",
            "description": "Paid Emergency Leave",
            "payrollCodes": [
                {
                    "publicId": "PRC594",
                    "name": "PEL",
                    "description": "Paid Emergency Leave",
                    "enabled": true
                }
            ],
            "payRate": 100,
            "enabled": true,
            "internalPayCode": "PEL",
            "CountedTowardsOverTime": false
        },
        {
            "publicId": "PC567",
            "name": "Paid Not Earned",
            "description": "Paid Not Earned",
            "payrollCodes": [
                {
                    "publicId": "PRC595",
                    "name": "PNE",
                    "description": "Paid Not Earned",
                    "enabled": true
                }
            ],
            "payRate": 100,
            "enabled": true,
            "internalPayCode": "PNE",
            "CountedTowardsOverTime": false
        },
        {
            "publicId": "PC568",
            "name": "Post Accident - Paid Time",
            "description": "Post Accident - Paid Time",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 100,
            "enabled": true,
            "internalPayCode": "ACCDNT",
            "CountedTowardsOverTime": false
        },
        {
            "publicId": "PC569",
            "name": "Accident Reporting Time",
            "description": "Accident Reporting Time",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 100,
            "enabled": true,
            "internalPayCode": "ACRPT",
            "CountedTowardsOverTime": "2"
        },
        {
            "publicId": "PC570",
            "name": "Bereavement Leave",
            "description": "Bereavement Leave",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 101,
            "enabled": true,
            "internalPayCode": "BRVPD",
            "CountedTowardsOverTime": "0"
        },
        {
            "publicId": "PC571",
            "name": "FMLA Personal Day",
            "description": "FMLA Personal Day",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 102,
            "enabled": true,
            "internalPayCode": "FMLAP",
            "CountedTowardsOverTime": "2"
        },
        {
            "publicId": "PC572",
            "name": "Jury Duty Paid",
            "description": "Jury Duty Paid",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 107,
            "enabled": true,
            "internalPayCode": "JURY",
            "CountedTowardsOverTime": "0"
        },
        {
            "publicId": "PC573",
            "name": "Parental Leave Paid",
            "description": "Parental Leave Paid",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 108,
            "enabled": true,
            "internalPayCode": "LOAPL",
            "CountedTowardsOverTime": false
        },
        {
            "publicId": "PC574",
            "name": "Union Business Paid",
            "description": "Union Business Paid",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 109,
            "enabled": true,
            "internalPayCode": "LOAUB",
            "CountedTowardsOverTime": false
        },
        {
            "publicId": "PC575",
            "name": "Military Leave Paid",
            "description": "Military Leave Paid",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 110,
            "enabled": true,
            "internalPayCode": "MILV",
            "CountedTowardsOverTime": false
        },
        {
            "publicId": "PC576",
            "name": "Minimum Guarantee Week",
            "description": "Minimum Guarantee Week",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 111,
            "enabled": true,
            "internalPayCode": "MIMW",
            "CountedTowardsOverTime": false
        },
        {
            "publicId": "PC577",
            "name": "Minimum Guarantee Daily",
            "description": "Minimum Guarantee Daily",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 112,
            "enabled": true,
            "internalPayCode": "MIND",
            "CountedTowardsOverTime": false
        },
        {
            "publicId": "PC578",
            "name": "Sick Other (Family)",
            "description": "Sick Other (Family)",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 113,
            "enabled": true,
            "internalPayCode": "SCKOP",
            "CountedTowardsOverTime": false
        },
        {
            "publicId": "PC579",
            "name": "Sick - Self",
            "description": "Sick - Self",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 114,
            "enabled": true,
            "internalPayCode": "SCKSP",
            "CountedTowardsOverTime": "0"
        },
        {
            "publicId": "PC580",
            "name": "Shift Premium",
            "description": "Shift Premium",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 115,
            "enabled": true,
            "internalPayCode": "SPRE",
            "CountedTowardsOverTime": false
        },
        {
            "publicId": "PC581",
            "name": "Vacation",
            "description": "Vacation",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 116,
            "enabled": true,
            "internalPayCode": "VACA",
            "CountedTowardsOverTime": "2"
        },
        {
            "publicId": "PC582",
            "name": "Volunteer Time",
            "description": "Volunteer Time",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 116,
            "enabled": true,
            "internalPayCode": "VOL",
            "CountedTowardsOverTime": "2"
        },
        {
            "publicId": "PC583",
            "name": "Student Operator Training",
            "description": "Student Operator Training",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 117,
            "enabled": true,
            "internalPayCode": "TRAIN",
            "CountedTowardsOverTime": false
        },
        {
            "publicId": "PC584",
            "name": "Instructor Time",
            "description": "Instructor Time",
            "payrollCodes": [
                {
                    "publicId": "PRC597",
                    "name": "RGT",
                    "description": "Instructor Time",
                    "enabled": true
                }
            ],
            "payRate": 118,
            "enabled": true,
            "internalPayCode": "INST",
            "CountedTowardsOverTime": "0"
        },
        {
            "publicId": "PC585",
            "name": "Spread Time Premium",
            "description": "Spread Time Premium",
            "payrollCodes": [
                {
                    "publicId": "PRC614",
                    "name": "SPD",
                    "description": "Spreadtime Paid Base Rate",
                    "enabled": true
                }
            ],
            "payRate": 119,
            "enabled": true,
            "internalPayCode": "SPD",
            "CountedTowardsOverTime": false
        },
        {
            "publicId": "PC586",
            "name": "Productivity Incentive",
            "description": "Productivity Incentive",
            "payrollCodes": [
                {
                    "publicId": "PRC589",
                    "name": "REG",
                    "description": "Regular Pay",
                    "enabled": true
                }
            ],
            "payRate": 0,
            "enabled": true,
            "internalPayCode": "PRODINC",
            "CountedTowardsOverTime": "2"
        }
    ]

    const toInsert = [];

    for (const pc of paycodes) {
        // Increment sequence for each paycode
        const nextSeq = await getNext(conn, 'PC');

        const newPublicId = `PC${nextSeq.sequenceNumber}`;

        // Transform payroll codes (remove id fields)
        const payrollCodes = (pc.payrollCodes || []).map((p) => ({
            publicId: p.publicId,
            name: p.name,
            description: p.description,
            enabled: p.enabled ?? true,
            createdBy: adminUserId,
            createdOn: new Date(),
            updatedBy: adminUserId,
            updatedOn: new Date()
        }));

        const doc = {
            publicId: newPublicId,
            name: pc.name,
            description: pc.description,
            payrollCodes,
            payRate: pc.payRate ?? 0,
            enabled: true,
            internalPayCode: pc.internalPayCode,
            CountedTowardsOverTime: pc.CountedTowardsOverTime ?? false,
            createdBy: null,
            createdOn: new Date(),
            updatedBy: null,
            updatedOn: new Date()
        };

        toInsert.push(doc);
    }

    // Insert all docs at once
    await WfmPayCode.insertMany(toInsert);

    // 4�⃣ Department / Activity type base sample

    const departments = [
        {
            "listitemvalue": "HR001",
            "listitemtext": "Human Resources"
        },
        {
            "listitemvalue": "FIN001",
            "listitemtext": "Finance & Accounting"
        },
        {
            "listitemvalue": "DEV001",
            "listitemtext": "Software Development"
        },
        {
            "listitemvalue": "QA001",
            "listitemtext": "Quality Assurance"
        },
        {
            "listitemvalue": "OPS001",
            "listitemtext": "Operations"
        },
        {
            "listitemvalue": "MKT001",
            "listitemtext": "Marketing"
        },
        {
            "listitemvalue": "IT001",
            "listitemtext": "Information Technology"
        },
        {
            "listitemvalue": "EREP001",
            "listitemtext": "Ereps"
        },
        {
            "listitemvalue": "D015",
            "listitemtext": "Production / Manufacturing"
        }
    ]

    const docs = departments.map(dep => ({
        listitemvalue: dep.listitemvalue,
        listitemtext: dep.listitemtext,
        tenantId,
        createdOn: new Date(),
        updatedOn: new Date()
    }));

    // Remove existing department codes for this tenant to prevent duplication
    await WfmDepartment.deleteMany({
        tenantId,
        listitemvalue: { $in: departments.map(d => d.listitemvalue) }
    });

    await WfmDepartment.insertMany(docs);

    // Generate sequential employee IDs
    const nextEmp1 = await getNext(conn, "E");   // 100001
    const nextEmp2 = await getNext(conn, "E");   // 100002

    // Build employee docs
    const employees = [
        {
            publicId: `E${nextEmp1}`,
            employeeId: `EMP${nextEmp1}`,
            firstName: "admin",
            lastName: "noah",
            tenantId,
            regDayOff: [],
            department: "HR001",
            createdOn: new Date(),
            createdBy: null,

        },
        {
            publicId: `E${nextEmp2}`,
            employeeId: `EMP${nextEmp2}`,
            firstName: tenantId,
            lastName: "admin",
            tenantId,
            regDayOff: [],
            department: "FIN001",
            createdOn: new Date(),
            createdBy: null,

        }
    ];

    // Insert into wfm_employees
    await Employee.insertMany(employees);
    return true;
}
async function iotLookupSeeder(conn, tenantId) {
    const IoTEventLookup = conn.model(
        'iot_eventLookup',
        IoTEventLookupSchema
    );
    const IoTEventSchemas = conn.model(
        'iot_events',
        IoTEventSchema
    );

    const defaults = [
        {
            event_type: 'face_recognition',
            emotion_code: null,
            description: 'Face detected/recognized'
        },
        {
            event_type: 'emotion_monitoring',
            emotion_code: 'EV2.1',
            description: 'Neutral'
        },
        {
            event_type: 'emotion_monitoring',
            emotion_code: 'EV2.2',
            description: 'Happiness'
        },
        {
            event_type: 'emotion_monitoring',
            emotion_code: 'EV2.3',
            description: 'Anger'
        },
        {
            event_type: 'emotion_monitoring',
            emotion_code: 'EV2.4',
            description: 'Contempt'
        },
        {
            event_type: 'emotion_monitoring',
            emotion_code: 'EV2.5',
            description: 'Disgust'
        },
        {
            event_type: 'emotion_monitoring',
            emotion_code: 'EV2.6',
            description: 'Fear'
        },
        {
            event_type: 'emotion_monitoring',
            emotion_code: 'EV2.7',
            description: 'Sadness'
        },
        {
            event_type: 'emotion_monitoring',
            emotion_code: 'EV2.8',
            description: 'Surprise'
        }
    ];


    for (const record of defaults) {
        await IoTEventLookup.updateOne(
            {
                emotion_code: record.emotion_code
            },
            {
                $setOnInsert: record
            },
            {
                upsert: true
            }
        );
    }

    console.log(`✓ iot_eventLookup seeded for tenant ${tenantId}`);
}
async function iotDeviceSeeder(conn, tenantId) {
    const IoTDevice = conn.model(
        'iot_devices',
        IoTDeviceSchema
    );

    const devices = [
        {
            device_id: 'camera-001',
            display_name: 'Front Door Camera',
            device_category: 'camera',
            capability: 'face_recognition',
            location_label: 'Main entrance outside',
            location_geo: [-71.0590, 42.3602],
            status: 'online',
            last_heartbeat_at: new Date()
        }
    ];

    for (const device of devices) {
        await IoTDevice.updateOne(
            { device_id: device.device_id },
            {
                $setOnInsert: device
            },
            {
                upsert: true
            }
        );
    }

    console.log(`✓ iot_devices seeded for tenant ${tenantId}`);
}
const ConfigSchema = new mongoose.Schema({
    _id:    { type: String },
    client: {
        id:             { type: String },
        secret:         { type: String },
        registeredAt:   { type: Date },
        lastTokenAt:    { type: Date }
    },
    jwt: {
        accessSecret:   { type: String },
        accessTtl:      { type: String },
        refreshSecret:  { type: String },
        refreshTtl:     { type: String }
    },
    admin: {
        secret: { type: String }
    },
    loconav: {
        userAuthentication: { type: String, default: "" }
    },
    createdAt: { type: Date, default: Date.now },
    updatedAt: { type: Date, default: Date.now }
}, { collection: "iot_security" });

const DataConnectionSchema = new mongoose.Schema({
    name:           { type: String, required: true },
    connectionInfo: {
        type:        { type: String },
        host:        { type: String },
        port:        { type: Number },
        database:    { type: String },
        serviceName: { type: String, default: null },
        sid:         { type: String, default: null }
    },
    credentials: {
        username: { type: String },
        password: { type: String }
    },
    businessObjects:  { type: [mongoose.Schema.Types.Mixed], default: [] },
    conceptMapping:   { type: mongoose.Schema.Types.Mixed, default: {} },
    metadata: {
        tablesCount:   { type: Number, default: 0 },
        objectsCount:  { type: Number, default: 0 },
        lastAnalyzed:  { type: Date },
        lastConnected: { type: Date }
    },
    createdAt: { type: Date, default: Date.now },
    updatedAt: { type: Date, default: Date.now }
}, { collection: "controlops_database_registry" });

async function configSeeder(conn, tenantId) {
    const Config = conn.model("iot_security", ConfigSchema);

    const doc = {
        _id: "config",
        client: {
            id:           tenantId,
            secret:       process.env.IOT_CLIENT_SECRET || crypto.randomBytes(30).toString("hex"),
            registeredAt: new Date(),
            lastTokenAt:  new Date()
        },
        jwt: {
            accessSecret:  "",
            accessTtl:     "90d",
            refreshSecret: "",
            refreshTtl:    "270d"
        },
        admin: {
            secret: process.env.IOT_ADMIN_SECRET || crypto.randomBytes(18).toString("hex")
        },
        loconav: {
            userAuthentication: ""
        },
        createdAt: new Date(),
        updatedAt: new Date()
    };

    await Config.updateOne(
        { _id: "config" },
        { $setOnInsert: doc },
        { upsert: true }
    );

    console.log(`? configs seeded for tenant ${tenantId}`);
}

async function dataConnectionSeeder(conn, tenantId, configData = {}) {
    const DataConnection = conn.model("controlops_database_registry", DataConnectionSchema);

    const doc = {
        name: "Qraie Ticketing DB",
        businessObjects: [
            { name: "Ticket",     description: "A request or issue raised by a user within the system",       relatedTo: ["Reporter","Assignee","Comments","Attachments","Status","Priority"] },
            { name: "Member",     description: "A user or employee involved in the system",                   relatedTo: ["Tickets","Comments","Attachments"] },
            { name: "Project",    description: "A work initiative or assignment linked to a ticket",          relatedTo: ["Ticket","Meta Info"] },
            { name: "Attachment", description: "A file attached to a ticket or comment",                     relatedTo: ["Ticket","Comment"] },
            { name: "Comment",    description: "A message or note added to a ticket or comment",             relatedTo: ["Ticket","Attachments"] },
            { name: "Issue Type", description: "A category or classification of a ticket",                   relatedTo: ["Ticket"] }
        ],
        conceptMapping: {
            tables: [
                { tableName: "ASK",                          concept: "Ticket",           description: "A request or issue raised by a user within the system" },
                { tableName: "ASK_ATTACHMENT",               concept: "Attachment",       description: "A file attached to a ticket or comment" },
                { tableName: "ASK_COMMENT_ATTACHMENT_STORE", concept: "Attachment",       description: "A file attached to a comment in a ticket" },
                { tableName: "ASK_COMMENTS",                 concept: "Comment",          description: "A message or note added to a ticket or comment" },
                { tableName: "ASK_HANDLER",                  concept: "Project Handler",  description: "Information about who handles or owns a ticket" },
                { tableName: "ISSUE_TYPE_LOOKUP",            concept: "Issue Type",       description: "A category or classification of a ticket" }
            ],
            columns: [
                { tableName: "ASK", columnName: "ask_id",            concept: "Ticket ID",            description: "Unique identifier for a ticket" },
                { tableName: "ASK", columnName: "tenant_id",         concept: "Tenant ID",            description: "Identifier for the tenant or organization" },
                { tableName: "ASK", columnName: "summary",           concept: "Summary",              description: "Brief description of the ticket" },
                { tableName: "ASK", columnName: "reporter",          concept: "Reporter",             description: "Member who raised the ticket" },
                { tableName: "ASK", columnName: "escalation_level",  concept: "Escalation Level",    description: "How high the priority of the ticket is" },
                { tableName: "ASK", columnName: "module",            concept: "Module",               description: "The system module where the ticket originated" },
                { tableName: "ASK", columnName: "description_detail",concept: "Detailed Description", description: "Full details of the ticket issue" },
                { tableName: "ASK", columnName: "ETA",               concept: "Estimated Time of Arrival", description: "Expected time for resolution" },
                { tableName: "ASK", columnName: "project_name",      concept: "Project Name",         description: "Name of the project associated with the ticket" },
                { tableName: "ASK", columnName: "status",            concept: "Status",               description: "Current state of the ticket" },
                { tableName: "ASK", columnName: "priority",          concept: "Priority",             description: "Urgency level of the ticket" },
                { tableName: "ASK", columnName: "created_by",        concept: "Creator",              description: "Member who created the ticket" },
                { tableName: "ASK", columnName: "created_at",        concept: "Creation Time",        description: "Timestamp when the ticket was created" },
                { tableName: "ASK", columnName: "modified_by",       concept: "Modified By",          description: "Member who last updated the ticket" },
                { tableName: "ASK", columnName: "modified_at",       concept: "Last Modified Time",   description: "Timestamp when the ticket was last updated" },
                { tableName: "ASK", columnName: "issue_type_id",     concept: "Issue Type ID",        description: "Reference to the type of issue" },
                { tableName: "ASK_ATTACHMENT", columnName: "attachment_id", concept: "Attachment ID", description: "Unique identifier for an attachment" },
                { tableName: "ASK_ATTACHMENT", columnName: "ask_id",        concept: "Ticket ID",     description: "Reference to the ticket the attachment belongs to" },
                { tableName: "ASK_ATTACHMENT", columnName: "tenant_id",     concept: "Tenant ID",     description: "Identifier for the tenant or organization" },
                { tableName: "ASK_ATTACHMENT", columnName: "file_name",     concept: "File Name",     description: "Name of the attached file" },
                { tableName: "ASK_ATTACHMENT", columnName: "file_path",     concept: "File Path",     description: "Path where the file is stored" },
                { tableName: "ASK_ATTACHMENT", columnName: "file_type",     concept: "File Type",     description: "MIME type or format of the file" },
                { tableName: "ASK_ATTACHMENT", columnName: "file_size",     concept: "File Size",     description: "Size of the attached file in bytes" },
                { tableName: "ASK_ATTACHMENT", columnName: "file_hash",     concept: "File Hash",     description: "Unique hash value for file integrity" },
                { tableName: "ASK_ATTACHMENT", columnName: "created_by",    concept: "Creator",       description: "Member who uploaded the attachment" },
                { tableName: "ASK_ATTACHMENT", columnName: "created_at",    concept: "Upload Time",   description: "Timestamp when the attachment was uploaded" },
                { tableName: "ASK_ATTACHMENT", columnName: "modified_by",   concept: "Modified By",   description: "Member who last updated the attachment" },
                { tableName: "ASK_ATTACHMENT", columnName: "modified_at",   concept: "Last Modified Time", description: "Timestamp when the attachment was last updated" },
                { tableName: "ASK_COMMENT_ATTACHMENT_STORE", columnName: "comment_attachment_id", concept: "Comment Attachment ID", description: "Unique identifier for an attachment in a comment" },
                { tableName: "ASK_COMMENT_ATTACHMENT_STORE", columnName: "comment_id",  concept: "Comment ID",  description: "Reference to the comment the attachment belongs to" },
                { tableName: "ASK_COMMENT_ATTACHMENT_STORE", columnName: "file_name",   concept: "File Name",   description: "Name of the attached file in a comment" },
                { tableName: "ASK_COMMENT_ATTACHMENT_STORE", columnName: "file_path",   concept: "File Path",   description: "Path where the file is stored in a comment" },
                { tableName: "ASK_COMMENT_ATTACHMENT_STORE", columnName: "file_type",   concept: "File Type",   description: "MIME type or format of the file in a comment" },
                { tableName: "ASK_COMMENT_ATTACHMENT_STORE", columnName: "file_size",   concept: "File Size",   description: "Size of the attached file in bytes" },
                { tableName: "ASK_COMMENT_ATTACHMENT_STORE", columnName: "file_hash",   concept: "File Hash",   description: "Unique hash value for file integrity in a comment" },
                { tableName: "ASK_COMMENT_ATTACHMENT_STORE", columnName: "is_deleted",  concept: "Is Deleted",  description: "Flag indicating if the attachment is deleted" },
                { tableName: "ASK_COMMENT_ATTACHMENT_STORE", columnName: "created_by",  concept: "Creator",     description: "Member who uploaded the attachment in a comment" },
                { tableName: "ASK_COMMENT_ATTACHMENT_STORE", columnName: "created_at",  concept: "Upload Time", description: "Timestamp when the attachment was uploaded in a comment" },
                { tableName: "ASK_COMMENTS", columnName: "comment_id",        concept: "Comment ID",        description: "Unique identifier for a comment" },
                { tableName: "ASK_COMMENTS", columnName: "ask_id",            concept: "Ticket ID",         description: "Reference to the ticket the comment belongs to" },
                { tableName: "ASK_COMMENTS", columnName: "tenant_id",         concept: "Tenant ID",         description: "Identifier for the tenant or organization" },
                { tableName: "ASK_COMMENTS", columnName: "member_id",         concept: "Member",            description: "User who posted the comment" },
                { tableName: "ASK_COMMENTS", columnName: "comment_text",      concept: "Comment Text",      description: "Text content of the comment" },
                { tableName: "ASK_COMMENTS", columnName: "comment_type",      concept: "Comment Type",      description: "Type of comment" },
                { tableName: "ASK_COMMENTS", columnName: "parent_comment_id", concept: "Parent Comment ID", description: "Reference to the comment that this comment replies to" },
                { tableName: "ASK_COMMENTS", columnName: "is_deleted",        concept: "Is Deleted",        description: "Flag indicating if the comment is deleted" },
                { tableName: "ASK_COMMENTS", columnName: "created_by",        concept: "Creator",           description: "Member who created the comment" },
                { tableName: "ASK_COMMENTS", columnName: "created_at",        concept: "Creation Time",     description: "Timestamp when the comment was created" },
                { tableName: "ASK_COMMENTS", columnName: "modified_by",       concept: "Modified By",       description: "Member who last updated the comment" },
                { tableName: "ASK_COMMENTS", columnName: "modified_at",       concept: "Last Modified Time",description: "Timestamp when the comment was last updated" },
                { tableName: "ASK_HANDLER", columnName: "ask_handler_id", concept: "Handler ID",    description: "Unique identifier for the handler" },
                { tableName: "ASK_HANDLER", columnName: "tenant_id",      concept: "Tenant ID",     description: "Identifier for the tenant or organization" },
                { tableName: "ASK_HANDLER", columnName: "ask_id",         concept: "Ticket ID",     description: "Reference to the ticket handled" },
                { tableName: "ASK_HANDLER", columnName: "meta_info_id",   concept: "Meta Info ID",  description: "Reference to project metadata" },
                { tableName: "ASK_HANDLER", columnName: "created_by",     concept: "Creator",       description: "Member who created the handler record" },
                { tableName: "ASK_HANDLER", columnName: "created_at",     concept: "Creation Time", description: "Timestamp when the handler record was created" },
                { tableName: "ASK_HANDLER", columnName: "modified_by",    concept: "Modified By",   description: "Member who last updated the handler record" },
                { tableName: "ASK_HANDLER", columnName: "modified_at",    concept: "Last Modified Time", description: "Timestamp when the handler record was last updated" },
                { tableName: "ASK_HANDLER", columnName: "ask_handler",    concept: "Handler",       description: "Member assigned to handle the ticket" },
                { tableName: "ASK_HANDLER", columnName: "ask_owner",      concept: "Owner",         description: "Member assigned as owner of the ticket" }
            ]
        },
        connectionInfo: {
            type:        "sqlserver",
            host:        configData.DB_HOST,
            port:        Number(configData.DB_PORT),
            database:    configData.DB_NAME || tenantId,
            serviceName: null,
            sid:         null
        },
        createdAt: new Date(),
        credentials: {
            username: configData.DB_USER,
            password: configData.DB_PASSWORD
        },
        metadata: {
            tablesCount:   5,
            objectsCount:  6,
            lastAnalyzed:  new Date(),
            lastConnected: new Date()
        },
        updatedAt: new Date()
    };

    await DataConnection.updateOne(
        { name: doc.name },
        { $setOnInsert: doc },
        { upsert: true }
    );

    console.log(`? data_connections seeded for tenant ${tenantId}`);
}

/* ===========================
   CONFIG
=========================== */

const SALT_ROUNDS = 10;
const DEFAULT_TZ = "UTC";

/* ===========================
   HELPERS
=========================== */

function generateRandomPassword(length = 16) {
    const chars =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789@#$%";
    return Array.from({ length }, () =>
        chars.charAt(Math.floor(Math.random() * chars.length))
    ).join("");
}

// QRaie-Database-Default-Inserts.sql reuses the same {...tenantid...}
// placeholder in two incompatible contexts: bare SQL identifiers (`use
// {...tenantid...};`, `INSERT INTO {...tenantid...}.dbo.X`, and inside
// IDENT_CURRENT('{...tenantid...}.dbo.X')) and quoted string-literal values
// (`N'{...tenantid...}'`, e.g. the MEMBER.username/TENANT.tenant_id columns).
// applySqlTemplate()'s generic key->value substitution can't tell these
// apart and would inject a bare, unbracketed value everywhere -- fine for a
// legacy tenant ID without special characters, but a tenant slug like
// "bridge-meta-test-16" then makes SQL Server parse `INSERT INTO
// bridge-meta-test-16.dbo.X` as a subtraction expression ("Incorrect syntax
// near '-'"). Must run BEFORE applySqlTemplate() so only the identifier
// occurrences get bracketed -- every remaining {...tenantid...} afterward is
// inside a quoted string literal, where the generic bare substitution is
// correct as-is.
function applyInsertsIdentifierFixups(template, tenantId) {
    return template
        .replaceAll(`{...tenantid...}.dbo`, `[${tenantId}].dbo`)
        .replace(/\buse\s+\{\.\.\.tenantid\.\.\.\}/gi, `use [${tenantId}]`);
}

function applySqlTemplate(template, values) {
    let sql = template;

    for (const [key, value] of Object.entries(values)) {
        const regex = new RegExp(`\\{\\.\\.\\.${key}\\.\\.\\.\\}`, "g");
        sql = sql.replace(regex, value);
    }

    return sql;
}

async function executeSqlScript(sqlText, sqlDB, tenantId) {
    const dbConfig = {
        user: sqlDB.DB_USER,
        password: sqlDB.DB_PASSWORD,
        server: sqlDB.DB_HOST,
        // Without this, the mssql package silently falls back to its
        // default port 1433 -- fine on bare metal where MSSQL listens on
        // the standard port directly, but this environment reaches it via
        // a NodePort remap (30143 -> 1433), so the real port has to be
        // explicit.
        port: parseInt(sqlDB.DB_PORT, 10),
        database: tenantId,
        options: {
            // sqlcmd -C negotiates TLS and just trusts the server's
            // certificate without strict validation -- encrypt: false here
            // (as originally written) refuses TLS entirely, which this
            // server appears to reject at login (surfaced as a generic
            // "Login failed" rather than an encryption-specific error).
            // Matching sqlcmd's actual behavior instead of its flag name.
            encrypt: true,
            trustServerCertificate: true
        },
        pool: {
            max: 10,
            min: 0,
            idleTimeoutMillis: 30000
        },
        requestTimeout: 300000,   // 5 minutes
        connectionTimeout: 30000 // 30 seconds
    };


    const pool = await sql.connect(dbConfig);
    try {
        // GO is a batch separator understood only by client tools
        // (sqlcmd/SSMS), which split on it and send each batch to the
        // server separately -- the server itself doesn't recognize GO as
        // valid T-SQL and rejects it ("Incorrect syntax near 'GO'") if sent
        // verbatim in one .batch() call. The schema script has one real GO
        // (after `USE [tenantId]`, once the CREATE DATABASE header block is
        // stripped above) -- split on any/all of them and run each batch in
        // order on the same connection, matching what sqlcmd -i does.
        const batches = sqlText
            .split(/^\s*GO\s*$/im)
            .map((b) => b.trim())
            .filter((b) => b.length > 0);
        for (const batch of batches) {
            await pool.request().batch(batch);
        }
    } finally {
        await pool.close();
    }
}


// Applies QRaie-Table-Script-*.sql (CREATE TABLE statements) against the
// tenant's database. The legacy bare-metal system ran this step itself,
// in bash, via `sqlcmd -i` after a `sed`-based placeholder substitution
// (see meta-builder's sibling 02_sql_mongo.sh reference copy) -- this is
// that exact same substitution logic ported to JS, since this job has no
// bash/sqlcmd step of its own (everything runs through this one Node
// script + the `mssql` package instead). Must run before
// runTenantDefaultInserts() -- the inserts reference tables this creates.
function applySchemaTemplate(template, tenantId) {
    let sqlText = template
        .replaceAll(`[{...tenantid...}]`, `[${tenantId}]`)
        // Must stay bracketed like every other identifier substitution below --
        // an unbracketed tenantId containing a hyphen (e.g. a tenant slug like
        // "bridge-meta-test-16") makes SQL Server parse `CREATE TABLE
        // bridge-meta-test-16.dbo.X` as a subtraction expression, surfacing as
        // "Incorrect syntax near '-'." The legacy bash script (02_sql_mongo.sh)
        // has this identical bug, just never triggered since legacy tenant IDs
        // were bare names without hyphens -- our tenant slugs always have one.
        .replaceAll(`{...tenantid...}.dbo`, `[${tenantId}].dbo`)
        .replaceAll(`{...tenantid...}`, `[${tenantId}]`)
        .replaceAll(`{...password...}`, `[2b10hashedPasswordHere]`);

    // Strip the DB-creation IF NOT EXISTS...GO block(s) -- the database
    // already exists by the time this runs (meta_builder_job.py's Job
    // creates it first, and provisioner.py blocks on that Job actually
    // finishing before this one is even triggered -- see
    // bridge_meta_builder_job.py).
    sqlText = sqlText.replace(/IF NOT EXISTS[\s\S]*?\r?\nGO\r?\n/g, "");

    sqlText = sqlText
        .replace(new RegExp(`use '${tenantId}'`, "gi"), `use [${tenantId}]`)
        .replace(new RegExp(`'${tenantId}'\\.dbo`, "gi"), `[${tenantId}].dbo`)
        .replaceAll(`IDENT_CURRENT('${tenantId}.dbo.`, `IDENT_CURRENT('[${tenantId}].dbo.`)
        // Remove the brackets back out of tenant STRING LITERALS (as
        // opposed to identifiers) -- e.g. a value column shouldn't end up
        // literally containing "[tenantid]".
        .replaceAll(`'[${tenantId}]'`, `'${tenantId}'`);

    return sqlText;
}

async function runTenantSchema({ tenantId, sqlDB }) {
    if (!/^[a-zA-Z0-9_-]+$/.test(tenantId)) {
        throw new Error("Invalid tenantId");
    }

    const schemaPath = path.resolve(__dirname, "QRaie-Table-Script-11Sep25.sql");
    if (!fs.existsSync(schemaPath)) {
        throw new Error(`Schema SQL file not found at ${schemaPath}`);
    }

    const schemaTemplate = fs.readFileSync(schemaPath, "utf8");
    const finalSql = applySchemaTemplate(schemaTemplate, tenantId);

    await executeSqlScript(finalSql, sqlDB, tenantId);
}

async function runTenantDefaultInserts({
    tenantId,
    dbName,
    tenantName,
    email,
    adminPassword,
    sqlDB,
    timezone = DEFAULT_TZ
}) {
    if (!/^[a-zA-Z0-9_-]+$/.test(tenantId)) {
        throw new Error("Invalid tenantId");
    }
    if (!/^[a-zA-Z0-9_-]+$/.test(dbName)) {
        throw new Error("Invalid dbName");
    }

    /* 🔐 Generate secrets */
    const qraieBotPwd = generateRandomPassword();
    const qraieWebPwd = generateRandomPassword();

    const qraieBotPwdHash = await bcrypt.hash(qraieBotPwd, SALT_ROUNDS);
    const qraieWebPwdHash = await bcrypt.hash(qraieWebPwd, SALT_ROUNDS);
    const adminPasswordHash = await bcrypt.hash(adminPassword, SALT_ROUNDS);

    const qraieBotUuid = uuidv4();
    const qraieWebUuid = uuidv4();

    /* 📄 Load SQL template */
    const sqlPath = path.resolve(__dirname, "QRaie-Database-Default-Inserts.sql");

    if (!fs.existsSync(sqlPath)) {
        throw new Error(`SQL file not found at ${sqlPath}`);
    }

    const sqlTemplate = fs.readFileSync(sqlPath, "utf8");

    /* 🔁 Replace placeholders -- tenantid here is the bare tenant name
       (business-data value, matches Mongo's tenantObj.tenantId convention).
       dbName (the SQL database/login name, which is the same bare tenant
       name for new tenants) is only used below for the SQL identifier
       positions and the connection itself. tenantid_upper is the upper-cased
       tenant id the default-inserts script stores in its tenant_id columns. */
    const replacements = {
        tenantid: tenantId,
        tenantid_upper: tenantId.toUpperCase(),
        tenant_name: tenantName,
        email: email,
        password: adminPasswordHash,
        TZ: timezone,

        workplace_QraieBot_api_user_password: qraieBotPwdHash,
        workplace_QraieBot_api_uuid_key: qraieBotUuid,

        workplace_QraieWeb_api_user_password: qraieWebPwdHash,
        workplace_QraieWeb_api_uuid_key: qraieWebUuid
    };

    const identifierFixedSql = applyInsertsIdentifierFixups(sqlTemplate, dbName);
    const finalSql = applySqlTemplate(identifierFixedSql, replacements);

    // FOR TESTING
    // const outputDir = path.resolve(__dirname, "..", "sql", "output");
    // if (!fs.existsSync(outputDir)) {
    //     fs.mkdirSync(outputDir, { recursive: true });
    // }

    // // 📄 output file name
    // const outputFilePath = path.join(
    //     outputDir,
    //     `QRaie-Database-Default-Inserts_${tenantId}.sql`
    // );

    // // ✍️ write final SQL
    // fs.writeFileSync(outputFilePath, finalSql, "utf8");
    // END TESTING

    /* 🚀 Execute SQL */
    await executeSqlScript(finalSql, sqlDB, dbName);

    /* 🧾 Return credentials (DO NOT LOG IN PROD) */
    return {
        tenantId,
        adminEmail: email,
        adminPassword: adminPassword,
        qraieBot: {
            password: qraieBotPwd,
            hashPassword: qraieBotPwdHash,
            uuid: qraieBotUuid
        },
        qraieWeb: {
            password: qraieWebPwd,
            uuid: qraieWebUuid
        }
    };
}
async function updateGatewaySecretsInBridgeMeta(conn, tenantId, authTokenPassword) {
    const BridgeMetaInfo = conn.model("bridgeMetaInfo", BridgeSchema);

    await BridgeMetaInfo.updateOne(
        { "tenantObj.tenantId": tenantId.toUpperCase() },
        {
            $set: {
                "workplace.authTokenPassword": authTokenPassword,
                updatedAt: new Date()
            }
        }
    );

    console.log("🔐 Gateway authTokenPassword stored in BridgeMetaInfo");
}
async function main() {
    const START_TIME = new Date();

    try {
        console.log("=======================================");
        console.log("🚀 TENANT ONBOARDING STARTED");
        console.log("=======================================");

        /* =========================
           STEP 0: Read CLI Args
        ========================= */

        const TENANT_ID = process.argv[2];
        const DOMAIN = process.argv[3];
        const EMAIL = process.argv[4];
        const DISPLAY_NAME = process.argv[5];
        const ADMIN_PW = process.argv[6];
        const TENANT_NAME = process.argv[7];

        if (
            !TENANT_ID ||
            !DOMAIN ||
            !EMAIL ||
            !DISPLAY_NAME ||
            !ADMIN_PW ||
            !TENANT_NAME
        ) {
            console.error(`
❌ Invalid arguments

Usage:
node tenantBridgeMeta.js <tenantId> <domain> <email> <displayName> <password> <tenantName>
`);
            process.exit(1);
        }

        console.log("📥 Input received:");
        console.log("   Tenant ID      :", TENANT_ID);
        console.log("   Tenant Name    :", TENANT_NAME);
        console.log("   Domain         :", DOMAIN);
        console.log("   Email          :", EMAIL);


        // A pod's own IP tells you nothing about which environment it's
        // in (always something like 10.244.x.x regardless of stage vs.
        // prod) -- unlike the old bare-metal host, where the server's own
        // 192.168.85.x address was a usable signal. tenant-operator
        // already knows which environment it itself serves
        // (settings.environment), so it passes that through directly.
        const isStaging = process.env.IS_STAGING === "true";

        console.log("🌍 Environment:", isStaging ? "STAGING" : "PRODUCTION");


        // Canonical tenant (infra-safe)
        const tenantId = TENANT_ID.toLowerCase();

        /* =========================
           STEP 1: Mongo Connection
        ========================= */

        console.log("🔌 Connecting to tenant MongoDB...");
        const conn = await getTenantConn(tenantId);
        console.log("✅ MongoDB connection established");

        /* =========================
           STEP 2: Bridge Meta Builder
        ========================= */

        console.log("⚙️ STEP 2: Building BridgeMetaInfo...");
        const configFile = await bridgeMetaBuilderService(
            conn,
            tenantId,
            TENANT_NAME,
            DOMAIN,
            EMAIL,
            isStaging
        );
        console.log("✅ BridgeMetaInfo saved");



        /* =========================
           STEP 3: SQL Schema + Default Inserts
        ========================= */

        const { DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME } = configFile;

        console.log("   SQL Host :", DB_HOST);
        console.log("   SQL Port :", DB_PORT);
        console.log("   SQL User :", DB_USER);
        console.log("   SQL DB   :", DB_NAME);

        // DB_NAME is the real database/login meta_builder_job.py created --
        // the bare tenant name (e.g. "hbss"), the same value as tenantId
        // above for tenants created after the DB-name change (older tenants
        // were created with the sequence-suffixed slug, e.g. "hbss-15",
        // which is why this stays a separate value). The schema script
        // never stores tenantId as a data VALUE (only as identifiers), so
        // DB_NAME alone is correct for runTenantSchema. The inserts script
        // does both (identifiers AND business-data values like
        // MEMBER.username/TENANT.tenant_id) -- runTenantDefaultInserts
        // takes both and keeps them separate, so those stored values stay
        // the bare tenantId, matching Mongo's own tenantObj.tenantId
        // convention, while the identifiers/connection still use DB_NAME.
        console.log("📄 STEP 3a: Running SQL schema (CREATE TABLE)...");
        await runTenantSchema({
            tenantId: DB_NAME,
            sqlDB: { DB_USER, DB_PASSWORD, DB_HOST, DB_PORT }
        });
        console.log("✅ SQL schema applied");

        console.log("📄 STEP 3b: Running SQL default inserts...");
        const QraieCreds = await runTenantDefaultInserts({
            tenantId,
            dbName: DB_NAME,
            tenantName: TENANT_NAME,
            email: EMAIL,
            adminPassword: ADMIN_PW,
            sqlDB: { DB_USER, DB_PASSWORD, DB_HOST, DB_PORT }
        });

        /* =========================
           STEP 4: Mongo Tenant Init
        ========================= */

        console.log("🗄️ STEP 3: Initializing Mongo tenant DB...");
        await tenantDatabaseInitializer(
            conn,
            tenantId,
            TENANT_NAME,
            ADMIN_PW,
            EMAIL,
            DOMAIN,
            QraieCreds.qraieBot.password,
            isStaging,
            configFile
        );
        console.log("✅ Mongo tenant DB initialized");


        console.log("✅ SQL default inserts completed");
        await updateGatewaySecretsInBridgeMeta(
            await getTenantConn(tenantId),
            tenantId,
            QraieCreds.qraieBot.hashPassword
        );
        /* =========================
           DONE
        ========================= */

        const END_TIME = new Date();
        const durationSec = ((END_TIME - START_TIME) / 1000).toFixed(2);

        console.log("=======================================");
        console.log("🎉 TENANT ONBOARDING COMPLETED");
        console.log("⏱️  Duration:", durationSec, "seconds");
        console.log("=======================================");

        process.exit(0);

    } catch (err) {
        console.error("=======================================");
        console.error("❌ TENANT ONBOARDING FAILED");
        console.error("---------------------------------------");
        console.error("Error:", err.message || err);
        console.error("=======================================");
        process.exit(1);
    }
}





main();