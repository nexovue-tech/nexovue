# Docker

Set the variables at `docker run` time, not in the Dockerfile. An `ENV` line in a
Dockerfile is baked into the image, and the image is a thing you push to a
registry: `docker history` prints every `ENV` value, so a key put there is a key
published to everyone who can pull the image.

## Passing them in

```bash
docker run \
  -e OTEL_SERVICE_NAME=checkout \
  -e OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.nexovue.com \
  -e OTEL_EXPORTER_OTLP_HEADERS="x-nexovue-key=$NEXOVUE_KEY" \
  -e OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
  checkout:1.4.0
```

Quote the headers value. It contains an `=` inside the value, and an unquoted
shell will not always hand it over intact.

## Keeping the key out of your shell history

An `--env-file` is the smaller of the two evils, because the file can have
restricted permissions and is not in your history:

```bash
cat > /etc/nexovue.env <<'EOF'
OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.nexovue.com
OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_xxxxxxxxxxxxxxxxxxxxxx
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
EOF
chmod 600 /etc/nexovue.env

docker run --env-file /etc/nexovue.env -e OTEL_SERVICE_NAME=checkout checkout:1.4.0
```

Note the quoting on the heredoc: `<<'EOF'` with the quotes stops the shell
expanding anything inside. Without them a key containing a `$` would be mangled,
and you would spend an afternoon on a 401.

Do not put an `--env-file` path inside the build context. `docker build` sends
the whole context to the daemon, and a `.env` sitting next to the Dockerfile ends
up in it.

## `ENV` in a Dockerfile is fine for the two that are not secret

```dockerfile
ENV OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
    OTEL_SERVICE_NAME=checkout
```

The endpoint and the protocol are not secret and do not change between
deployments of the same service, so baking them in is reasonable. The key is the
only value that must come in at run time.

## Auto-instrumentation inside the image

The agent or launcher has to be in the image and on the start command. The
pattern per language:

```dockerfile
# Node
ENV NODE_OPTIONS="--require @opentelemetry/auto-instrumentations-node/register"
CMD ["node", "app.js"]
```

```dockerfile
# Python
CMD ["opentelemetry-instrument", "python", "app.py"]
```

```dockerfile
# Java
COPY opentelemetry-javaagent.jar /opt/opentelemetry-javaagent.jar
ENTRYPOINT ["java", "-javaagent:/opt/opentelemetry-javaagent.jar", "-jar", "/app.jar"]
```

Go needs no agent — the setup is compiled in. See [go.md](../languages/go.md).

## Pointing a container at a local nexovue

If you are running nexovue locally and the service is in a container,
`localhost` inside that container is the container, not your machine. The
receiver is not there and the connection is refused.

On Docker Desktop, the host is reachable as `host.docker.internal`:

```bash
docker run -e OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:14318 ...
```

On plain Linux Docker that name is not defined by default, and you have to add
it:

```bash
docker run --add-host host.docker.internal:host-gateway \
  -e OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:14318 ...
```

This is the single most common reason a containerised service sends nothing to a
local receiver while the same service sends fine when run on the host.

## Checking it from inside the container

If nothing arrives, prove the container can reach the endpoint at all before
looking at anything else:

```bash
docker exec -it <container> sh -c \
  'curl -sS -o /dev/null -w "%{http_code}\n" -X GET $OTEL_EXPORTER_OTLP_ENDPOINT/v1/traces'
```

`405` means you reached the receiver — the path exists and takes POST only,
which is exactly right. Anything else, and the problem is the network or the
endpoint rather than your instrumentation. [troubleshooting.md](../troubleshooting.md)
covers the rest.
