# Releases and deploys

nexovue shows each release of each service — when it started, how far it rolled out,
and how its failure share and latency compare with the version before it. It learns
about releases in two ways, and you can use either or both:

| Way in | What it gives | What it takes |
|---|---|---|
| **A build id in `service.version`** | Releases appear on their own, from your traffic. Rolling deploys are compared version against version, side by side | One change per service |
| **Deploys reported by your CI** | Failed and rolled-back deploys, redeploys of the same version, a link to the commit, and releases for services whose traffic carries no version | A secret, and one step in your pipeline |

Releases record what happened in order. They do not say why a number moved.

## A build id in `service.version`

`service.version` is a standard OpenTelemetry resource attribute. Set it from the same
build variable your deploy already knows — an image tag or a commit — so every build
reports a different value. **Never a fixed value such as `latest`**: one value that
never changes reads as one release that never ends, and nothing can be compared.

This is one change per service. The examples below use `$GIT_SHA` for whatever your
build calls its id.

**Kubernetes, with Helm.** In the container's `env`, from the chart's own image tag:

```yaml
            - name: OTEL_RESOURCE_ATTRIBUTES
              value: service.version={{ .Values.image.tag }}
```

and deploy with `helm upgrade --install checkout ./chart --set image.tag=$GIT_SHA`.

**Kubernetes, with Kustomize.** Generate a ConfigMap from your pipeline and read it
into the container:

```bash
kustomize edit add configmap checkout-version --from-literal=SERVICE_VERSION=$GIT_SHA
```

```yaml
            - name: SERVICE_VERSION
              valueFrom:
                configMapKeyRef:
                  name: checkout-version
                  key: SERVICE_VERSION
            - name: OTEL_RESOURCE_ATTRIBUTES
              value: service.version=$(SERVICE_VERSION)
```

**Docker.** Pass the id as a build argument, so the image carries its own version:

```dockerfile
ARG BUILD_VERSION
ENV OTEL_RESOURCE_ATTRIBUTES=service.version=${BUILD_VERSION}
```

```bash
docker build --build-arg BUILD_VERSION=$GIT_SHA -t checkout:$GIT_SHA .
```

**systemd.** Have the deploy step write the version into an environment file the unit
reads, next to the ingest key's:

```bash
echo "OTEL_RESOURCE_ATTRIBUTES=service.version=$GIT_SHA,deployment.environment.name=production" \
  | sudo tee /etc/nexovue/version.env
```

```ini
EnvironmentFile=/etc/nexovue/version.env
```

Only server traces and queue consumers are counted. Traffic from a browser key never
makes a release, whatever version it names.

## One project per environment

Use one nexovue project, with its own ingest key and its own deploy secret, for each
environment: production, staging, and so on. A release compares this environment's
traffic with itself; two environments in one project mix a staging rollout into
production's numbers, and a deploy reported for one is shown against the other's
traffic. Nothing stops two environments sharing a project, so keep them apart here.

## Reporting deploys from CI

1. Open **Settings → Projects**, choose the project, and find **Deploys**. Only owners
   and admins see it.
2. Mint a secret. It is shown once: store it straight away in your CI's secret store as
   `NEXOVUE_DEPLOY_SECRET`. If it is lost, mint another.
3. Copy a recipe below (the console shows each with your project's address filled in;
   here `https://nexovue.example.com/api/v1/deploys/webhook/PROJECT_ID` stands for it)
   and add it after your deploy step.
4. Run the pipeline with the dry run on. The Deploys section shows **Last check
   received** change within a few seconds. Nothing is stored by a dry run.
5. Remove the `NEXOVUE_DRY_RUN` line. The next deploy appears on **Releases**, beside
   what your traffic shows.

Every recipe:

- **never fails your deploy.** Each attempt has 10 seconds, the retries 30 in all, a
  refusal prints a warning, and the step always exits 0;
- **reads the secret from an environment variable**, never as a command argument,
  where other processes on the runner could read it;
- **builds the delivery id from the service and the attempt.** A retry inside one run
  resends the same id, which is stored once; a re-run of the pipeline is a new deploy;
- **reports the same value your build sets as `service.version`**, so the deploy and the
  traffic name the release alike;
- **links the commit page** from the CI's own variables, and sends nothing about who ran
  it.

The runner needs `curl` and `python3`, which GitHub's and GitLab's standard images have.

### GitHub Actions

```yaml
- name: Report the deploy to nexovue
  env:
    NEXOVUE_DEPLOY_SECRET: ${{ secrets.NEXOVUE_DEPLOY_SECRET }}
    NEXOVUE_SERVICE: checkout
    # The same value your build passes as service.version.
    NEXOVUE_VERSION: ${{ github.sha }}
    NEXOVUE_DELIVERY_ID: checkout-${{ github.run_id }}-${{ github.run_attempt }}
    NEXOVUE_COMMIT: ${{ github.sha }}
    NEXOVUE_COMMIT_URL: ${{ github.server_url }}/${{ github.repository }}/commit/${{ github.sha }}
    # Remove once the dry run shows as "last check received".
    NEXOVUE_DRY_RUN: "true"
  run: |
    set +e
    body=$(printf '{"dry_run":%s,"deploys":[{"delivery_id":"%s","service_name":"%s","version":"%s","deployed_at":"%s","commit_sha":"%s","url":"%s"}]}' \
      "${NEXOVUE_DRY_RUN:-false}" "$NEXOVUE_DELIVERY_ID" "$NEXOVUE_SERVICE" "$NEXOVUE_VERSION" \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$NEXOVUE_COMMIT" "$NEXOVUE_COMMIT_URL")
    signature=$(printf '%s' "$body" | python3 -c 'import hashlib, hmac, os, sys; print(hmac.new(os.environ["NEXOVUE_DEPLOY_SECRET"].encode(), sys.stdin.buffer.read(), hashlib.sha256).hexdigest())')
    status=$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 \
      --retry 2 --retry-connrefused --retry-max-time 30 --header 'Content-Type: application/json' \
      --header "X-Hub-Signature-256: sha256=$signature" --data "$body" \
      "https://nexovue.example.com/api/v1/deploys/webhook/PROJECT_ID")
    case "$status" in
      2??) echo "nexovue: deploy reported (HTTP $status)" ;;
      *) echo "::warning::nexovue did not record this deploy (HTTP $status); the deploy itself is unaffected" ;;
    esac
    exit 0
```

### GitLab CI

Add `NEXOVUE_DEPLOY_SECRET` as a masked CI/CD variable; GitLab hands it to the job as
an environment variable.

```yaml
report-deploy:
  stage: .post
  variables:
    NEXOVUE_SERVICE: checkout
    # The same value your build passes as service.version.
    NEXOVUE_VERSION: $CI_COMMIT_SHA
    NEXOVUE_DELIVERY_ID: checkout-$CI_JOB_ID
    NEXOVUE_COMMIT: $CI_COMMIT_SHA
    NEXOVUE_COMMIT_URL: $CI_PROJECT_URL/-/commit/$CI_COMMIT_SHA
    # Remove once the dry run shows as "last check received".
    NEXOVUE_DRY_RUN: "true"
  script:
    - |
      set +e
      body=$(printf '{"dry_run":%s,"deploys":[{"delivery_id":"%s","service_name":"%s","version":"%s","deployed_at":"%s","commit_sha":"%s","url":"%s"}]}' \
        "${NEXOVUE_DRY_RUN:-false}" "$NEXOVUE_DELIVERY_ID" "$NEXOVUE_SERVICE" "$NEXOVUE_VERSION" \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$NEXOVUE_COMMIT" "$NEXOVUE_COMMIT_URL")
      signature=$(printf '%s' "$body" | python3 -c 'import hashlib, hmac, os, sys; print(hmac.new(os.environ["NEXOVUE_DEPLOY_SECRET"].encode(), sys.stdin.buffer.read(), hashlib.sha256).hexdigest())')
      status=$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 \
        --retry 2 --retry-connrefused --retry-max-time 30 --header 'Content-Type: application/json' \
        --header "X-Hub-Signature-256: sha256=$signature" --data "$body" \
        "https://nexovue.example.com/api/v1/deploys/webhook/PROJECT_ID")
      case "$status" in
        2??) echo "nexovue: deploy reported (HTTP $status)" ;;
        *) echo "::warning::nexovue did not record this deploy (HTTP $status); the deploy itself is unaffected" ;;
      esac
      exit 0
```

### curl

```bash
# NEXOVUE_DEPLOY_SECRET comes from your secret store, never from this file.
export NEXOVUE_SERVICE=checkout
export NEXOVUE_VERSION="$BUILD_VERSION"   # the same value as service.version
export NEXOVUE_DELIVERY_ID="checkout-$BUILD_ID-$BUILD_ATTEMPT"
export NEXOVUE_COMMIT="$GIT_COMMIT"
export NEXOVUE_COMMIT_URL="https://git.example.com/acme/checkout/commit/$GIT_COMMIT"
export NEXOVUE_DRY_RUN=true   # remove once the dry run shows as "last check received"
set +e
body=$(printf '{"dry_run":%s,"deploys":[{"delivery_id":"%s","service_name":"%s","version":"%s","deployed_at":"%s","commit_sha":"%s","url":"%s"}]}' \
  "${NEXOVUE_DRY_RUN:-false}" "$NEXOVUE_DELIVERY_ID" "$NEXOVUE_SERVICE" "$NEXOVUE_VERSION" \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$NEXOVUE_COMMIT" "$NEXOVUE_COMMIT_URL")
signature=$(printf '%s' "$body" | python3 -c 'import hashlib, hmac, os, sys; print(hmac.new(os.environ["NEXOVUE_DEPLOY_SECRET"].encode(), sys.stdin.buffer.read(), hashlib.sha256).hexdigest())')
status=$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 \
  --retry 2 --retry-connrefused --retry-max-time 30 --header 'Content-Type: application/json' \
  --header "X-Hub-Signature-256: sha256=$signature" --data "$body" \
  "https://nexovue.example.com/api/v1/deploys/webhook/PROJECT_ID")
case "$status" in
  2??) echo "nexovue: deploy reported (HTTP $status)" ;;
  *) echo "::warning::nexovue did not record this deploy (HTTP $status); the deploy itself is unaffected" ;;
esac
exit 0
```

## What a deploy carries

The signed body is `{"deploys": [ ... ]}`, one to 100 items, and at most 512 KiB.
`"dry_run": true` checks everything and stores nothing.

| Field | Required | Rule |
|---|---|---|
| `delivery_id` | Yes | 1 to 200 characters, unique to this deploy. Sending the same id again stores nothing and answers with what was stored |
| `service_name`, `version` | Yes | Up to 200 characters, as your traffic names them. Control, direction-changing and tag characters are refused, and so is `__other__` |
| `deployed_at` | Yes | When it happened, with a time zone. At most 5 minutes ahead of our clock and at most 7 days behind it, so past deploys older than a week cannot be imported |
| `state` | No | `started`, `finished` (the default), `failed` or `rolled_back` |
| `commit_sha` | No | 40 or 64 hexadecimal characters |
| `url` | No | An `https` link with no username, password, query or fragment, up to 2,048 characters, such as the commit page. nexovue never fetches it |
| `source` | No | A label of up to 64 characters, `webhook` by default |
| `metadata` | No | Flat text keys and values, at most 1 KiB |

Each item is answered with whether the service is known to the project, whether that
version has been seen in its traffic, and the versions that have — so a misspelt name
shows in your CI's own output.

**One bad item refuses the whole request**, naming the item and the field, and the
Deploys section shows it as the last refusal. A wrong signature is not shown there,
since anyone on the internet could send one: your CI's output shows a 401.

A project stores at most 1,000 new deploys an hour. A batch that would pass that is
refused whole with a `Retry-After`, and resent deploys do not count.

## What is stored, and what is not

Deploys are stored in clear text, exactly as sent, for as long as the project keeps its
derived data. Credentials in `metadata` are masked before storing, by the same rules as
telemetry, but **do not post CI context objects** — the GitHub `github` object, GitLab's
predefined variables — into `metadata`: they are stored, can be shown to your team, and
can name people. The recipes above send none.

Deploy secrets are sealed: nobody, owners included, can read one back.

## What must be reachable

Your CI must reach the console's address over HTTPS. A hosted runner cannot reach an
install on a private network. Two routes: run the step on a self-hosted runner inside
that network, or skip deploys and rely on `service.version` alone, which needs nothing
from CI.

## Counts assume you do not sample before sending

Release numbers count every server request that reaches nexovue. If your SDK or
Collector samples before sending, every count is short by that factor, and nothing in
the data shows it. Turn sampling off in the SDK and the Collector; nexovue samples what
it stores after counting.

## Rotating a secret, and removing what a leaked one sent

The Deploys section numbers the steps: mint a new secret, put it in your CI's secret
store, deploy once and watch **used** move to the new secret, then withdraw the old one.
Withdrawing first would refuse every deploy in between.

If a secret leaked, withdraw it and tick **also remove what it sent since**, with the
time it could first have been used. What it sent from then on is removed and the
releases are worked out again; what other secrets sent stays. An owner or admin can also
remove a single deploy with `DELETE /api/v1/deploys/{deploy_id}`. Both are recorded in
the audit log.

## After a deploy: reading the comparison from your pipeline

Your pipeline can read the same comparison the release page shows and decide for
itself. The deploy's answer carries each item's `release_id`; wait for the rollout,
then read it with an API token ([api.md](api.md)):

```bash
sleep 600
curl --silent --max-time 10 --header "Authorization: Bearer $NEXOVUE_TOKEN" \
  "https://nexovue.example.com/api/v1/releases/$RELEASE_ID/comparison?window=1" \
  | python3 -c 'import json, sys; c = json.load(sys.stdin); print(c["reason"], [(b["kind"], b["method"], b["failure_share"]["reading"]) for b in c["blocks"]])'
```

It answers with each block's method, its counts, and a plain reading — `higher`,
`lower`, `about_same` or `not_clearly_different` — or the reason it cannot compare yet.
It compares numbers; it does not say why they moved, and a pipeline that rolls back on
it should treat `not_clearly_different` as "not enough traffic yet", not as "fine".
