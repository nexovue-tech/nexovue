# Kubernetes

The key goes in a Secret. The endpoint goes in a ConfigMap or straight into the
Deployment, because it is not secret and it is the same for every workload in
the cluster.

## The Secret

```bash
kubectl create secret generic nexovue-ingest \
  --namespace production \
  --from-literal=key='nvk_live_xxxxxxxxxxxxxxxxxxxxxx'
```

Single quotes, so the shell does not touch the value.

Creating it from the command line keeps it out of Git. If your cluster is
managed by GitOps and everything must be in a repository, encrypt it — Sealed
Secrets, SOPS, External Secrets Operator — rather than committing a plain
Secret. A Kubernetes Secret is base64, which is an encoding and not encryption:
anybody who can read the manifest can read the key.

## The Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: checkout
spec:
  template:
    spec:
      containers:
        - name: checkout
          image: checkout:1.4.0
          env:
            - name: OTEL_SERVICE_NAME
              value: checkout
            - name: OTEL_EXPORTER_OTLP_ENDPOINT
              value: https://ingest.nexovue.com
            - name: OTEL_EXPORTER_OTLP_PROTOCOL
              value: http/protobuf
            - name: NEXOVUE_KEY
              valueFrom:
                secretKeyRef:
                  name: nexovue-ingest
                  key: key
            - name: OTEL_EXPORTER_OTLP_HEADERS
              value: x-nexovue-key=$(NEXOVUE_KEY)
```

The `$(NEXOVUE_KEY)` on the last line is Kubernetes' own variable expansion, not
the shell's. It works because `NEXOVUE_KEY` is defined earlier in the same `env`
list — Kubernetes expands references to variables declared before the one using
them, and leaves a forward reference as a literal `$(NEXOVUE_KEY)` string. Order
matters, and it fails as a 401 rather than as an error.

## Attaching the pod's identity to the telemetry

This is worth doing once and it pays for itself the first time you need to know
which pod produced a trace:

```yaml
            - name: OTEL_RESOURCE_ATTRIBUTES
              value: k8s.namespace.name=$(NAMESPACE),k8s.pod.name=$(POD_NAME),k8s.node.name=$(NODE_NAME),service.version=1.4.0
            - name: NAMESPACE
              valueFrom:
                fieldRef:
                  fieldPath: metadata.namespace
            - name: POD_NAME
              valueFrom:
                fieldRef:
                  fieldPath: metadata.name
            - name: NODE_NAME
              valueFrom:
                fieldRef:
                  fieldPath: spec.nodeName
```

Same ordering rule: the referenced variables must be declared before the line
that uses them.

`service.version` is what lets nexovue separate "the service is slow" from "the
release we shipped at 14:00 is slow".

## The OpenTelemetry Operator

If you are instrumenting more than a handful of services, the operator removes
the per-Deployment work. It injects the language agent into the pod and sets
every variable from one cluster-level resource.

Install the operator, then:

```yaml
apiVersion: opentelemetry.io/v1alpha1
kind: Instrumentation
metadata:
  name: nexovue
  namespace: production
spec:
  exporter:
    endpoint: https://ingest.nexovue.com
  env:
    - name: OTEL_EXPORTER_OTLP_PROTOCOL
      value: http/protobuf
    - name: NEXOVUE_KEY
      valueFrom:
        secretKeyRef:
          name: nexovue-ingest
          key: key
    - name: OTEL_EXPORTER_OTLP_HEADERS
      value: x-nexovue-key=$(NEXOVUE_KEY)
  propagators:
    - tracecontext
    - baggage
```

Then annotate the workloads you want instrumented, one line per Deployment:

```yaml
metadata:
  annotations:
    instrumentation.opentelemetry.io/inject-nodejs: "true"    # or -python, -java, -dotnet
```

There is no Go annotation that works this way — Go is compiled, so there is
nothing to inject. Go services set the variables themselves as in the Deployment
above.

> **Check this against your operator version.** The `Instrumentation` resource's
> API version and field names have changed across operator releases. The shape
> above is correct for the `v1alpha1` resource; confirm against the operator
> version you install.

## A Collector in the cluster

A Collector as a DaemonSet or a Deployment gives you one place holding the key,
one place to change the destination, and a buffer that survives a pod restart.
Workloads then export to it with no credential:

```yaml
            - name: OTEL_EXPORTER_OTLP_ENDPOINT
              value: http://otel-collector.observability.svc.cluster.local:4318
```

[collector.md](../collector.md) has the configuration, and the
`k8sattributes` processor that fills in pod, namespace and node metadata for
every workload without the `fieldRef` block above.

## Common mistakes

**A literal `$(NEXOVUE_KEY)` in the header.** The `env` entries are in the wrong
order. The Secret reference must come before the entry that uses it.

**Every pod reports as one service.** `OTEL_SERVICE_NAME` was set in a shared
ConfigMap rather than per Deployment.

**Nothing leaves the cluster.** A NetworkPolicy with a default-deny egress rule
needs an explicit allow for the ingest host on 443, and for DNS. This is a
common finding in a hardened cluster and produces exactly the same symptom as a
wrong key: silence.
