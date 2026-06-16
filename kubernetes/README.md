# Kubernetes deployment

A cloud-native alternative to the Ansible/Docker Compose deployment in `roles/`.
Same application (Guacamole + provisioning API + student sandboxes), running as
Kubernetes-native workloads instead of Docker Compose on two hand-provisioned VMs.

## What's different from the Compose deployment

- The provisioning API talks to the Kubernetes API instead of the Docker socket:
  each student session is a Pod + Service in a dedicated namespace, not a
  container on a Docker bridge network.
- Session/roster state lives in Postgres (`lab` schema) instead of an
  in-process dict, so the API can run multiple replicas behind one Service.
- A `CronJob` does TTL expiry and orphan cleanup once per tick, replacing the
  in-process polling thread (which would otherwise run once per API replica).
- Postgres is a [CloudNativePG](https://cloudnative-pg.io/) `Cluster`, fronted
  by a PgBouncer `Pooler`, instead of a single `postgres:16` container.
- TLS termination and `/guacamole` + `/api` routing move from nginx to an
  `Ingress` + Traefik `Middleware` (only the `/api` prefix needs stripping;
  `/guacamole` passes through unchanged, matching the old nginx config).
- The `JSON_SECRET_KEY` and a rotated `guacadmin` break-glass password are
  generated once at first deploy and stored as Secrets, instead of defaulting
  to a static zero-key / shipping the schema's well-known `guacadmin`/`guacadmin`
  row as-is.
- No custom Guacamole image: the stock `guacamole/guacamole` image already
  builds the `guacamole-auth-json` extension by default and auto-enables it
  from the `JSON_SECRET_KEY` environment variable.

## Prerequisites on the target cluster

- An ingress controller — the chart's `Ingress`/`Middleware` objects assume
  **Traefik**.
- [cert-manager](https://cert-manager.io/) with a `ClusterIssuer` (default
  name `platform-selfsigned`, override via `certIssuer`).
- The [CloudNativePG operator](https://cloudnative-pg.io/) installed
  cluster-wide (ships the `Cluster`/`Pooler` CRDs this chart's `Cluster` and
  `Pooler` objects need).
- [metrics-server](https://github.com/kubernetes-sigs/metrics-server) (or
  another `metrics.k8s.io` provider) if you want the `HorizontalPodAutoscaler`
  objects to actually scale anything — without it they're harmless but inert.

## Deploying

```bash
helm install lab-platform ./chart \
  --namespace lab-platform --create-namespace \
  --set hostname=lab.your-domain.example \
  --set image.api.tag=<published-tag> \
  --set image.bootstrap.tag=<published-tag> \
  --set image.studentSandbox.tag=<published-tag>
```

Or point an Argo CD `Application` at this directory the same way you'd point
one at any repo-local Helm chart — `spec.source.path: kubernetes/chart` with
`helm.valuesObject` for the cluster-specific overrides above.

## Images

`.github/workflows/kubernetes-images.yml` builds and pushes
`ghcr.io/<repo-owner>/lab-platform-{api,bootstrap,student}` on every push to
`main` that touches `kubernetes/`, tagged by short commit SHA. Pull requests
get a build-only validation run (no push, since fork PRs don't get package
write access).
