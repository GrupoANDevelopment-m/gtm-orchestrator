{{- define "speed-to-lead.name" -}}
speed-to-lead-agent
{{- end -}}

{{- define "speed-to-lead.labels" -}}
app.kubernetes.io/name: {{ include "speed-to-lead.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "speed-to-lead.selectorLabels" -}}
app.kubernetes.io/name: {{ include "speed-to-lead.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
