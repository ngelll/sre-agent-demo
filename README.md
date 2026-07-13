# SRE Agent Demo

Two Flask apps deployed to AKS, monitored by Azure Application Insights, watched by Azure SRE Agent.

## Apps

- **web-app-a** — checkout/order service, has `/break` (500) and `/crash` (unhandled exception).
- **web-app-b** — report generator, has `/leak` (CPU spike) and `/memhog` (memory leak).

## Demo Scenarios

### Scenario A — 5xx surge
```bash
for i in {1..50}; do curl -s $INGRESS/a/break; done
```
Expected: Application Insights alert fires within 3-5 minutes. SRE Agent picks up the alert, correlates logs, identifies `ORDER_PROCESSING_FAILED` pattern, opens a GitHub issue with the RCA.

### Scenario B — CPU spike
```bash
for i in {1..10}; do curl -s $INGRESS/b/leak; done
```
Expected: CPU on web-app-b pod hits ~100%. HorizontalPodAutoscaler triggers, scales from 1 pod to 3. SRE Agent notes the scale event and suggests the underlying root cause.

## Structure

```
apps/
  web-app-a/ ← Scenario A source
  web-app-b/ ← Scenario B source
k8s/
  ...       ← K8s manifests
```
