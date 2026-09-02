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
