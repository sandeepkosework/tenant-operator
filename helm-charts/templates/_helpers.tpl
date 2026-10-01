{{- define "tenant-operator.name" -}}
{{- .Chart.Name -}}
{{- end }}

{{- define "tenant-operator.fullname" -}}
{{- if .Release.Name | eq .Chart.Name -}}
{{- .Chart.Name -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end }}

{{- define "tenant-operator.labels" -}}
app.kubernetes.io/name: {{ include "tenant-operator.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: Helm
{{- end }}

{{- define "tenant-operator.secretName" -}}
{{- .Values.existingSecretName | default (printf "%s-secrets" (include "tenant-operator.fullname" .)) -}}
{{- end }}

{{- define "tenant-operator.postgresFullname" -}}
{{- printf "%s-postgres" (include "tenant-operator.fullname" .) -}}
{{- end }}

{{- define "tenant-operator.postgresSecretName" -}}
{{- .Values.postgresql.auth.existingSecretName | default (printf "%s-postgres" (include "tenant-operator.fullname" .)) -}}
{{- end }}

{{- /*
Resolves to the in-chart Postgres password, the same way every time it's
included within one render (secret.yaml and postgres-secret.yaml both call
it) -- see values.yaml's postgresql.auth.password comment for why this is a
derived value rather than `randAlphaNum` (which would render differently on
each of those two calls and break DATABASE_URL).
*/ -}}
{{- define "tenant-operator.postgresPassword" -}}
{{- if .Values.postgresql.auth.existingSecretName -}}
{{- $secret := lookup "v1" "Secret" .Release.Namespace .Values.postgresql.auth.existingSecretName -}}
{{- if $secret -}}
{{- index $secret.data "password" | b64dec -}}
{{- else -}}
{{- fail (printf "postgresql.auth.existingSecretName %q not found in namespace %q" .Values.postgresql.auth.existingSecretName .Release.Namespace) -}}
{{- end -}}
{{- else if .Values.postgresql.auth.password -}}
{{- .Values.postgresql.auth.password -}}
{{- else -}}
{{- printf "%s/%s/tenant-operator-postgres" .Release.Namespace .Release.Name | sha256sum | trunc 24 -}}
{{- end -}}
{{- end }}
