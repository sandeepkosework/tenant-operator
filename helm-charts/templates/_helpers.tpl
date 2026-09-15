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

{{- /*
Deliberately its own app.kubernetes.io/name (not tenant-operator.labels'
reused verbatim) -- the main app's Service selector is exactly
tenant-operator.labels, and Kubernetes Service selectors match ANY pod
carrying at least those labels. Reusing the same labels for the mongodb
Deployment's pod template would make the main Service's port-8000
endpoints silently include mongo's port-27017 pods too (and vice versa
for a mongodb Service using the same selector) -- a distinct name keeps
the two selector sets disjoint.
*/ -}}
{{- define "tenant-operator.mongodbLabels" -}}
app.kubernetes.io/name: {{ include "tenant-operator.name" . }}-mongodb
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: Helm
{{- end }}

{{- define "tenant-operator.mongodbSecretName" -}}
{{- .Values.mongodb.auth.existingSecretName | default (printf "%s-mongodb" (include "tenant-operator.fullname" .)) -}}
{{- end }}
