export function getActorsTemplate() {
    return [
        {
            actor_id: "qraie.sendDirectMessage",
            name: "qraie.sendDirectMessage",
            type: "socket",
            displayName: "Sending a Qraie DM",
            description: "Send direct message via Socket.io with authentication",
            enabled: true,
            hooks: {
                beforeRequest: {
                    type: "actor",
                    id: "socket.getAuthToken",
                    cache: {
                        enabled: true,
                        key: "qraie_socket_auth_token",
                        ttl: 3600
                    },
                    inject: {
                        "header.auth_token": "${result.message.access_token.message}"
                    }
                }
            },
            config: {
                url: "https://{{TENANT_ID}}-bridge{{STG_ENV}}.{{DOMAIN_NAME}}",
                event: "SendDirectMessage",
                data: {
                    header: { auth_token: "" },
                    data: {
                        sender_id: "${params.sender_id}",
                        receiver_id: "${params.receiver_id}",
                        tenant_id: "${params.tenant_id}",
                        message_text: "${params.message_text}",
                        parent_message_id: "${params.parent_message_id}",
                        attachments: "${params.attachments}"
                    }
                },
                connectionOptions: {
                    transports: ["websocket"],
                    reconnection: true,
                    reconnectionAttempts: 3,
                    reconnectionDelay: 1000,
                    timeout: 5000
                },
                timeout: 10000,
                retry: {
                    enabled: true,
                    maxAttempts: 2,
                    backoffMs: 1000
                }
            }
        },

        {
            actor_id: "socket.getAuthToken",
            name: "socket.getAuthToken",
            type: "socket",
            displayName: "Getting authorization to use qraie messaging",
            description: "Get authentication token via Socket.io",
            enabled: true,
            config: {
                url: "https://{{TENANT_ID}}-bridge{{STG_ENV}}.{{DOMAIN_NAME}}",
                event: "AuthToken",
                data: {
                    clientId: "QraieBot",
                    password: "{{QRAIBE_BOT_PASSWORD}}"
                },
                connectionOptions: {
                    transports: ["websocket"],
                    reconnection: true,
                    reconnectionAttempts: 3,
                    reconnectionDelay: 1000,
                    timeout: 5000
                },
                timeout: 10000,
                retry: {
                    enabled: true,
                    maxAttempts: 2,
                    backoffMs: 1000
                }
            }
        }
    ];
}
