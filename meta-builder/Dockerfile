FROM node:20-slim

WORKDIR /app

# Dependencies baked in at build time -- the old bare-metal script ran
# `npm install`/`npm ci` fresh on every single tenant provisioning (see
# 02_sql_mongo.sh's `rm -rf node_modules package-lock.json && npm install`),
# which is both slow and means a flaky npm registry could break tenant
# onboarding. Installing once here means every tenant run just starts the
# already-installed script.
COPY package.json package-lock.json* ./
RUN npm install --omit=dev

COPY tenantBridgeMeta.js bridge-schema-definition.js actor.template.js metaBridgeInfo_template.json ./
COPY QRaie-Table-Script-11Sep25.sql QRaie-Database-Default-Inserts.sql ./

ENTRYPOINT ["node", "tenantBridgeMeta.js"]
