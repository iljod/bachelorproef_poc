# bachelorproef-poc

Two deployment options:

- `roles/` + `site.yml` — Ansible/Docker Compose on two VMs.
- `kubernetes/` — cloud-native Helm chart for an existing Kubernetes cluster.
  See [`kubernetes/README.md`](kubernetes/README.md).

Both deployments run the **same** application, kept entirely in `core/` — there
is exactly one copy of every file. The two parts that genuinely differ between
Docker and Kubernetes, the sandbox orchestrator (containers vs Pods) and the
state store (in-memory vs Postgres), live in a single `services/orchestrator.py`
and `services/state_db.py` that contain *both* backends side by side and select
one at runtime from the `LAB_PLATFORM` environment variable
(`docker` or `kubernetes`). Each deployment just builds `core/` with its own
small Dockerfile (`Dockerfile.docker` / `Dockerfile.kubernetes`), which sets
that variable. So editing the provisioning logic means touching one file, and a
change can never apply to one platform but be forgotten on the other.
