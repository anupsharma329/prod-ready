# Monitoring setup

Prometheus and Grafana are managed by ArgoCD (chart versions pinned in argocd/*.yaml).

## Rebuilding on a fresh cluster

1. Create the namespace and the Grafana admin Secret (not stored in Git):

       kubectl create namespace monitoring
       kubectl create secret generic grafana-admin -n monitoring \
         --from-literal=admin-user=admin \
         --from-literal=admin-password=<choose-a-password>

2. Apply the Applications:

       kubectl apply -f argocd/dashboards-app.yaml -f argocd/prometheus-app.yaml -f argocd/grafana-app.yaml

3. Wait until all show Synced and Healthy:

       kubectl get applications -n argocd

## Notes

- Prometheus has no persistent disk and keeps 1 day of data, so history resets when its pod restarts.
- The sidecar, Alertmanager, Pushgateway, node-exporter and kube-state-metrics are disabled to keep load low on minikube.
