{{- define "pms.labels" -}}
app.kubernetes.io/name: {{ include "pms.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "pms.name" -}}
{{- .Chart.Name -}}
{{- end }}

{{/*
Spread replicas of a stateless workload across nodes.
Usage: include "pms.topologySpread" (dict "app" "backend" "root" $)
*/}}
{{- define "pms.topologySpread" -}}
{{- $ts := .root.Values.highAvailability.topologySpread -}}
{{- if $ts.enabled }}
topologySpreadConstraints:
  - maxSkew: {{ $ts.maxSkew }}
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: {{ $ts.whenUnsatisfiable }}
    labelSelector:
      matchLabels:
        app: {{ .app }}
{{- end }}
{{- end }}

{{/*
Evict pods from a failed node after N seconds instead of the default 300s.
*/}}
{{- define "pms.tolerations" -}}
{{- $s := .Values.highAvailability.nodeFailureTolerationSeconds -}}
tolerations:
  - key: node.kubernetes.io/unreachable
    operator: Exists
    effect: NoExecute
    tolerationSeconds: {{ $s }}
  - key: node.kubernetes.io/not-ready
    operator: Exists
    effect: NoExecute
    tolerationSeconds: {{ $s }}
{{- end }}

{{/*
HTTP startup/readiness/liveness probes against the named port "http".
Usage: include "pms.probes" .Values.backend.probes
*/}}
{{- define "pms.probes" -}}
startupProbe:
  httpGet:
    path: {{ .path }}
    port: http
  periodSeconds: {{ .startup.periodSeconds }}
  failureThreshold: {{ .startup.failureThreshold }}
readinessProbe:
  httpGet:
    path: {{ .path }}
    port: http
  periodSeconds: {{ .readiness.periodSeconds }}
  timeoutSeconds: {{ .readiness.timeoutSeconds }}
  failureThreshold: {{ .readiness.failureThreshold }}
livenessProbe:
  httpGet:
    path: {{ .path }}
    port: http
  periodSeconds: {{ .liveness.periodSeconds }}
  timeoutSeconds: {{ .liveness.timeoutSeconds }}
  failureThreshold: {{ .liveness.failureThreshold }}
{{- end }}
