{{/* Nom court du chart. */}}
{{- define "ollama-agent.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Nom complet de la release, tronqué à 63 caractères (limite DNS). */}}
{{- define "ollama-agent.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "ollama-agent.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "ollama-agent.labels" -}}
helm.sh/chart: {{ include "ollama-agent.chart" . }}
{{ include "ollama-agent.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "ollama-agent.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ollama-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: web
{{- end }}

{{- define "ollama-agent.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "ollama-agent.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/* --- Ollama embarqué --- */}}
{{- define "ollama-agent.ollama.fullname" -}}
{{- printf "%s-ollama" (include "ollama-agent.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "ollama-agent.ollama.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ollama-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: ollama
{{- end }}

{{- define "ollama-agent.ollama.labels" -}}
helm.sh/chart: {{ include "ollama-agent.chart" . }}
{{ include "ollama-agent.ollama.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* URL effective passée à l'app via OLLAMA_URL. */}}
{{- define "ollama-agent.ollamaUrl" -}}
{{- if .Values.ollama.enabled }}
{{- printf "http://%s:%v/api/chat" (include "ollama-agent.ollama.fullname" .) .Values.ollama.service.port }}
{{- else }}
{{- .Values.ollamaUrl }}
{{- end }}
{{- end }}
