# AWS ECS

A task definition has two places to put an environment variable, and the
difference is the whole of this page.

- `environment` — a literal in the task definition. The task definition is
  readable by anyone with `ecs:DescribeTaskDefinition`, and it is usually in
  version control.
- `secrets` — a reference to Secrets Manager or SSM Parameter Store, resolved by
  the ECS agent when the task starts. The value never appears in the task
  definition.

The endpoint goes in `environment`. The key goes in `secrets`.

## Store the key

```bash
aws secretsmanager create-secret \
  --name /nexovue/ingest-key \
  --secret-string 'nvk_live_xxxxxxxxxxxxxxxxxxxxxx'
```

SSM Parameter Store with `--type SecureString` works the same way and costs less
if you have many of them.

## The task definition

```json
{
  "family": "checkout",
  "containerDefinitions": [
    {
      "name": "checkout",
      "image": "123456789012.dkr.ecr.eu-west-1.amazonaws.com/checkout:1.4.0",
      "environment": [
        { "name": "OTEL_SERVICE_NAME", "value": "checkout" },
        { "name": "OTEL_EXPORTER_OTLP_ENDPOINT", "value": "https://ingest.nexovue.com" },
        { "name": "OTEL_EXPORTER_OTLP_PROTOCOL", "value": "http/protobuf" }
      ],
      "secrets": [
        {
          "name": "NEXOVUE_KEY",
          "valueFrom": "arn:aws:secretsmanager:eu-west-1:123456789012:secret:/nexovue/ingest-key"
        }
      ]
    }
  ]
}
```

## The part ECS does not do for you

ECS has no variable interpolation. There is no `$(NEXOVUE_KEY)` as there is in
Kubernetes: `secrets` puts the value in `NEXOVUE_KEY`, and nothing will assemble
`x-nexovue-key=` plus that value into `OTEL_EXPORTER_OTLP_HEADERS` on your
behalf.

Three ways out, in the order worth trying.

**Store the whole header value as the secret.** The simplest and the one to
reach for first. Put `x-nexovue-key=nvk_live_...` — the complete string — into
Secrets Manager, and name the environment variable
`OTEL_EXPORTER_OTLP_HEADERS`:

```json
      "secrets": [
        {
          "name": "OTEL_EXPORTER_OTLP_HEADERS",
          "valueFrom": "arn:aws:secretsmanager:eu-west-1:123456789012:secret:/nexovue/otlp-headers"
        }
      ]
```

The secret is now a header line rather than a key, which reads oddly the first
time. It is still one secret holding one credential, and it removes the only
moving part.

**Assemble it in the entrypoint.**

```dockerfile
ENTRYPOINT ["/bin/sh", "-c", "export OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=$NEXOVUE_KEY && exec node app.js"]
```

`exec` matters: without it the shell stays as PID 1 and your process never
receives the SIGTERM ECS sends at task stop, so it is killed after the timeout
instead of shutting down — which means the last batch of spans is lost on every
deploy.

**Run a Collector sidecar.** The container holding the credential is then the
Collector, your application container gets `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318`
and no key at all, and the same task definition works unchanged if you ever
change destinations. See [collector.md](../collector.md).

## Fargate

Everything above applies unchanged. Two extra things are worth knowing.

**Egress.** A task in a private subnet needs a NAT gateway or a VPC endpoint to
reach the ingest host. A task with `assignPublicIp: DISABLED` and no NAT route
sends nothing, and the failure looks identical to a wrong key.

**Task metadata.** Fargate exposes a metadata endpoint that the Collector's
`ecs` resource detector reads, filling in cluster, task ARN and task family as
resource attributes. That is a reason to run the sidecar: those attributes are
what let you tell two tasks of the same service apart.

## awslogs and the deploy window

ECS stops a task by sending SIGTERM and then SIGKILL after
`stopTimeout` — 30 seconds by default. An OpenTelemetry batch processor holds
spans for up to 5 seconds before sending, so a container that exits immediately
on SIGTERM drops whatever it was holding.

Shutting the tracer provider down on SIGTERM flushes it. The language pages show
where. It is a small thing that shows up as a gap in your traces at exactly the
moment you deploy, which is exactly when you are looking.
