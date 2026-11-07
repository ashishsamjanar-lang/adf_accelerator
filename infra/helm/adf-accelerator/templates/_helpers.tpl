{{/*
Base name for resources.
*/}}
{{- define "adf-accelerator.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fully qualified app name, used as a prefix for every resource this chart creates.
*/}}
{{- define "adf-accelerator.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "adf-accelerator.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels.
*/}}
{{- define "adf-accelerator.labels" -}}
helm.sh/chart: {{ include "adf-accelerator.chart" . }}
{{ include "adf-accelerator.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
Selector labels, parameterized by component (backend/worker/frontend).
*/}}
{{- define "adf-accelerator.selectorLabels" -}}
app.kubernetes.io/name: {{ include "adf-accelerator.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "adf-accelerator.componentLabels" -}}
{{ include "adf-accelerator.labels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "adf-accelerator.componentSelectorLabels" -}}
{{ include "adf-accelerator.selectorLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
ServiceAccount name.
*/}}
{{- define "adf-accelerator.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "adf-accelerator.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Name of the Secret the app containers read from — either the chart-managed
one (secrets.create: true) or a client-supplied existingSecret (Option B,
recommended for production; see values.yaml's `secrets` block).
*/}}
{{- define "adf-accelerator.secretName" -}}
{{- if .Values.secrets.create -}}
{{- printf "%s-secrets" (include "adf-accelerator.fullname" .) -}}
{{- else -}}
{{- required "secrets.existingSecret is required when secrets.create is false (see values.yaml's secrets block for both options)" .Values.secrets.existingSecret -}}
{{- end -}}
{{- end -}}

{{/*
Resolve a fully-qualified image reference from global.imageRegistry + a
per-component repository + the shared (or per-component) tag.
*/}}
{{- define "adf-accelerator.image" -}}
{{- $repo := .repository -}}
{{- if .global.imageRegistry -}}
{{- $repo = printf "%s%s" .global.imageRegistry .repository -}}
{{- end -}}
{{- $tag := .tag | default .chartAppVersion -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
