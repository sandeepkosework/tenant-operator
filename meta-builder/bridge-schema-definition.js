import mongoose from 'mongoose';
const { Schema } = mongoose;

const TenantSchema = new Schema({
  tenantId: { type: String, required: true, index: true },
  tenantName: String,
  emailId: String,
  logo: String,
}, { _id: false });

const WorkplaceSchema = new Schema({
  joinedOn: Date,
  PORT: String,
  redisPort: String,
  redisIp: String,
  redisPassword: String,
  serverIPA: String,
  RESPONSE_TIMEOUT: String,
  G_JWT_SECRETKEY: String,
  G_RT_SECRETKEY: String,
  G_JWT_EXPIRESIN: String,
  DB_HOST: String,
  DB_USER: String,
  DB_PASSWORD: String,
  DB_NAME: String,
  DB_SCHEMA: String,
  DB_PORT: String,
  NODE_ENV: String,
  MICROSERVICE_URL: String,
  syncInterval: String,
  MAX_CONNECTIONS: String,
  MIN_CONNECTIONS: String,
  alertMemberId: String,
  VIDEO_BASE_URL: String,
  gatewayUrl: String,
  authTokenPassword: String,
  authTokenUsername: String,
}, { _id: false });

const WfmDbConnSchema = new Schema(
  {
    type: {
      type: String,
      required: true
    },
    connectionString: {
      type: String,
      required: true
    }
  },
  { _id: false }
);

const TntDbConnSchema = new Schema(
  {
    wfm: {
      type: WfmDbConnSchema,
      required: true
    }
  },
  { _id: false }
);
const WfmSchema = new Schema(
  {
    NEXT_PUBLIC_SOCKET_URL: {
      type: String,
      required: true
    },
    FABREQ_COMMAND_ENDPOINT_MAP: {
      type: String,
      required: true
    },
    TNT_DB_CONN: {
      type: TntDbConnSchema,
      required: true
    }
  },
  { _id: false }
);
const ERepSchema = new Schema({
  name: String,
  email: String,
  createdAt: Date,
  eRepId: String,
  accessUrl: String,
  containerName: String,
  updatedAt: Date
}, { _id: false });

const CtrlOpsSchema = new Schema({
  REDIS_HOST: String,
  REDIS_PORT: String,
  REDIS_PASSWORD: String,
  DB_HOST: String,
  DB_USER: String,
  DB_PASSWORD: String,
  MONGODB_URI: String,
  DB_NAME: String
}, { _id: false });

const DlmHrDlmSchema = new Schema({
  llmBaseUrl: String,
  modelName: String,
  mcpServerUrl: String,
  ragEndpoint: String
}, { _id: false });

const DlmSchema = new Schema({
  hrDlm: DlmHrDlmSchema,
  chatUrl: String,
  secretKey: String,
  slmKbApiAuth: {
    authUser: String,
    authPassword: String
  }
}, { _id: false });


const AgentLookupSchema = new Schema({
  erepLanguage: Schema.Types.Mixed,
  additionalLanguages: Schema.Types.Mixed,
  turnEagerness: Schema.Types.Mixed,
  turnTimeoutduration: Schema.Types.Mixed
}, { _id: false });

const AgentSchema = new Schema({
  agentLookup: AgentLookupSchema
}, { _id: false });

const BridgeSchema = new Schema({
  tenantObj: TenantSchema,
  workplace: WorkplaceSchema,
  wfmObj: WfmSchema,
  eRepObj: [ERepSchema],
  ctrlOpsObj: CtrlOpsSchema,
  dlmObj: DlmSchema,
  createdAt: Date,
  updatedAt: Date,
  __v: Number,
  agentObj: AgentSchema
}, {
  collection: 'bridgeMetaInfo'
});



const PermissionSchema = new Schema({
  roleId: { type: String, required: true },
  hasAccess: { type: Boolean, default: false }
}, { _id: false });


const RoleSchema = new Schema({
  permissions: { type: [PermissionSchema], default: [] }
}, { _id: false });


const TokenSchema = new Schema({
  token: { type: String, required: true },
  createdOn: { type: Date, default: Date.now }
}, { _id: false });


const AuthSchema = new Schema({
  referenceId: { type: String, index: true },
  tenantId: { type: String, required: true, index: true },
  username: { type: String, required: true, unique: true },
  password: { type: String, required: true },
  salt: { type: String },
  roles: { type: [RoleSchema], default: [] },
  accessTokens: { type: [TokenSchema], default: [] },
  refreshTokens: { type: [TokenSchema], default: [] },
  createdOn: { type: Date, default: Date.now },
  updatedOn: { type: Date, default: Date.now }
}, {
  collection: 'auth'
});


// Update `updatedOn` on save
AuthSchema.pre('save', function (next) {
  this.updatedOn = new Date();
  next();
});


// Indexes
AuthSchema.index({ tenantId: 1, username: 1 }, { unique: true });


/** ==========================
* Sequence Schema
* ========================== */
const SequenceSchema = new Schema({
  sequenceCode: { type: String, required: true },
  sequenceName: { type: String, required: true },
  sequenceNumber: { type: Number, required: true }
}, {
  collection: 'sequences'
});


SequenceSchema.index({ sequenceCode: 1, sequenceName: 1 }, { unique: true });


/** ==========================
* User Schema
* ========================== */
const PhoneNumberSchema = new Schema({
  number: { type: String, required: true }
}, { _id: false });


const LoginCountSchema = new Schema({
  allowed: { type: Number, default: 0 },
  failed: { type: Number, default: 0 }
}, { _id: false });


const UserSchema = new Schema({
  userId: { type: String, required: true, index: true, unique: true },
  emailId: { type: String },
  fullName: { type: String },
  userName: { type: String, required: true },
  enabled2FA: { type: Boolean, default: false },
  createdOn: { type: Number, default: () => Date.now() },
  loginCount: { type: LoginCountSchema, default: () => ({ allowed: 0, failed: 0 }) },
  phoneNumbers: { type: [PhoneNumberSchema], default: [] }
}, {
  collection: 'users'
});


UserSchema.index({ userName: 1 });

/** ==========================
* ========================== */
const PayCodeSubTypeSchema = new Schema({
  name: { type: String, required: true },
  enabled: { type: Boolean, default: true },
  isTime: { type: Boolean, default: false },
  hours: { type: String }, // kept as string in source (eg. "0200")
  subTypePayCode: { type: String },
  countedTowardsOverTime: { type: Boolean, default: false }
}, { _id: true });


const WfmPayCodeSchema = new Schema({
  name: { type: String, required: true },
  description: { type: String },
  publicId: { type: String, index: true },
  startDate: { type: Date },
  endDate: { type: Date },
  subTypes: { type: [PayCodeSubTypeSchema], default: [] },
  enabled: { type: Boolean, default: true },
  color: { type: String },
  employeeTypeGroup: { type: String },
  clockInOutRequired: { type: Boolean, default: false },
  payCode: { type: String },
  autoAccept: { type: Boolean, default: false },
  createdBy: { type: Schema.Types.ObjectId, ref: 'User' },
  createdOn: { type: Date, default: Date.now },
  updatedOn: { type: Date },
  __v: { type: Number, select: false }
}, { collection: 'wfm_paycodes' });

WfmPayCodeSchema.index({ payCode: 1, publicId: 1 });

/** ==========================
* WFM Activity Types / Departments (detailed example)
* Example doc shows payrollCodes array and other flags
* ========================== */
const PayrollCodeSchema = new Schema({
  publicId: { type: String },
  name: { type: String },
  description: { type: String },
  enabled: { type: Boolean, default: true },
  createdBy: { type: Schema.Types.ObjectId, ref: 'User' },
  createdOn: { type: Date, default: Date.now },
  updatedBy: { type: Schema.Types.ObjectId, ref: 'User' },
  updatedOn: { type: Date }
}, { _id: true });

const WfmDepartmentSchema = new Schema(
  {
    listitemvalue: {
      type: String,
      required: true,
      trim: true
    },
    listitemtext: {
      type: String,
      required: true,
      trim: true
    },
    tenantId: {
      type: String,
      required: true,
      index: true
    },
    createdOn: {
      type: Date,
      default: Date.now
    },
    updatedOn: {
      type: Date,
      default: Date.now
    }
  },
  {
    collection: "wfm_departments", // change if needed
    versionKey: false
  }
);



/** ==========================
* Employee Schema
* ========================== */
const EmployeeSchema = new Schema({
  employeeId: { type: String, required: true, index: true },
  publicId: { type: String, index: true },
  firstName: { type: String, required: true },
  lastName: { type: String, required: true },
  jobTitle: { type: String },
  department: { type: String }, // reference to department code like HR001
  dateOfBirth: { type: String }, // stored as string in source doc
  gender: { type: String, enum: ['M', 'F', 'O'], default: 'O' },
  tenantId: { type: String, required: true },
  createdOn: { type: Date, default: Date.now },
  updatedOn: { type: Date, default: Date.now }
}, {
  collection: 'wfm_employees'
});


// Auto-update updatedOn timestamp
EmployeeSchema.pre('save', function (next) {
  this.updatedOn = new Date();
  next();
});


// Improve search performance
EmployeeSchema.index({ tenantId: 1, employeeId: 1 }, { unique: true });
EmployeeSchema.index({ lastName: 1, firstName: 1 });


const ActivitySubTypeSchema = new Schema(
  {
    name: { type: String, required: true },

    color: { type: String },

    enabled: { type: Boolean, default: true },

    isTime: { type: Boolean, default: false },

    hours: { type: String }, // kept string e.g. "0800"

    subTypePayCode: { type: String, required: true },

    countedTowardsOverTime: { type: Boolean, default: false }
  },
  {
    _id: true // allow Mongo to auto-generate
  }
);

/**
 * ============================
 * Activity Type Schema
 * ============================
 */
const WfmActivityTypeSchema = new Schema(
  {
    name: { type: String, required: true },

    description: { type: String },

    publicId: { type: String, required: true, index: true },

    startDate: { type: Date },

    endDate: { type: Date },

    subTypes: {
      type: [ActivitySubTypeSchema],
      default: []
    },

    enabled: { type: Boolean, default: true },

    color: { type: String },

    employeeTypeGroup: { type: String },

    clockInOutRequired: { type: Boolean, default: false },

    payCode: { type: String },

    autoAccept: { type: Boolean, default: false },

    createdOn: { type: Date, default: Date.now },

    updatedOn: { type: Date, default: Date.now },

    __v: { type: Number, select: false }
  },
  {
    collection: "wfm_activitytypes"
  }
);

/**
 * ============================
 * Indexes
 * ============================
 */
// WfmActivityTypeSchema.index({ publicId: 1 }, { unique: true });
WfmActivityTypeSchema.index({ name: 1 });

/**
 * Auto update updatedOn
 */
WfmActivityTypeSchema.pre("save", function (next) {
  this.updatedOn = new Date();
  next();
});


/**
 * ============================
 * CtrlOps Actors Schema
 * ============================
 */
const ActorSchema = new mongoose.Schema(
  {
    actor_id: {
      type: String,
      required: true,
      unique: true,
      index: true
    },

    name: {
      type: String
    },

    description: {
      type: String
    },

    displayName: {
      type: String
    },

    type: {
      type: String,
      required: true,
      enum: ["http", "oracle", "db", "socket", "webhook", "custom"],
      index: true
    },

    config: {
      type: Object,
      required: true
    },

    input_schema: {
      type: Object
    },

    output_schema: {
      type: Object
    },

    hooks: {
      type: Object
    },

    idempotency: {
      type: Object
    },

    metadata: {
      type: Object
    },

    enabled: {
      type: Boolean,
      default: true,
      index: true
    },

    createdOn: {
      type: Date,
      default: Date.now
    },

    updatedOn: {
      type: Date,
      default: Date.now
    }
  },
  {
    collection: "controlops_actors"
  }
);
ActorSchema.pre("save", function (next) {
  this.updatedOn = new Date();
  next();
});
ActorSchema.index({ type: 1 });
ActorSchema.index({ enabled: 1 });
ActorSchema.index({ "metadata.category": 1 });


const IoTDeviceSchema = new mongoose.Schema(
  {
    device_id: {
      type: String,
      required: true,
      unique: true,
      index: true
    },

    display_name: {
      type: String,
      required: true
    },

    device_category: {
      type: String,
      default: 'camera'
    },

    capability: {
      type: String,
      required: true,
      enum: ['face_recognition', 'emotion_monitoring'],
      index: true
    },

    location_label: {
      type: String
    },

    location_geo: {
      type: [Number], // [longitude, latitude]
      index: '2d'
    },

    status: {
      type: String,
      enum: ['online', 'offline', 'provisioning'],
      default: 'provisioning',
      index: true
    },

    last_heartbeat_at: {
      type: Date,
      default: null
    }
  },
  {
    collection: 'iot_devices',
    timestamps: {
      createdAt: 'created_at',
      updatedAt: 'updated_at'
    }
  }
);

const IoTEventSchema = new mongoose.Schema(
  {
    event_id: {
      type: String,
      required: true,
      unique: true,
      index: true
    },

    device_id: {
      type: String,
      required: true,
      index: true
    },

    event_type: {
      type: String,
      required: true,
      enum: ['face_recognition', 'emotion_monitoring']
    },

    event_timestamp: {
      type: Date,
      required: true,
      index: true
    },

    emotion_code: {
      type: String,
      default: null
    },

    metadata: {
      type: mongoose.Schema.Types.Mixed,
      default: {}
    }
  },
  {
    collection: 'iot_events',
    timestamps: {
      createdAt: 'created_at',
      updatedAt: false
    }
  }
);

/**
 * Compound indexes
 */
IoTEventSchema.index({ device_id: 1, event_timestamp: -1 });
IoTEventSchema.index({ created_at: -1 });

const IoTEventLookupSchema = new mongoose.Schema(
  {
    event_type: {
      type: String,
      required: true,
      enum: ['face_recognition', 'emotion_monitoring'],
      index: true
    },

    emotion_code: {
      type: String,
      unique: true,
      sparse: true, // allows null once
      default: null
    },

    description: {
      type: String,
      required: true
    }
  },
  {
    collection: 'iot_eventLookup',
    timestamps: false
  }
);


export {
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
  IoTEventSchema,
  IoTEventLookupSchema
};
