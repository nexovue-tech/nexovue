# Python

Python has zero-code instrumentation: a launcher called
`opentelemetry-instrument` starts your application with the libraries you
already use patched at runtime — Django, Flask, FastAPI, `requests`,
`psycopg`, SQLAlchemy, `redis`, `celery` and many more.

Start there. Write your own spans later, once you can see the automatic ones.

## Already exporting OpenTelemetry

Set the three variables and restart. Nothing else changes.

```
OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.nexovue.com
OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_xxxxxxxxxxxxxxxxxxxxxx
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
```

The protocol variable matters more in Python than in most languages. The
`opentelemetry-exporter-otlp` package installs both a gRPC and an HTTP exporter,
and which one is used when you do not say has differed between versions. Say it.

## Starting from nothing

### 1. Install

```bash
pip install opentelemetry-distro opentelemetry-exporter-otlp
```

`opentelemetry-distro` brings the API, the SDK and the two command-line tools
below. `opentelemetry-exporter-otlp` brings the exporters.

### 2. Install the instrumentation for what you actually use

```bash
opentelemetry-bootstrap -a install
```

This reads the packages installed in your environment and installs the matching
instrumentation for each — `opentelemetry-instrumentation-flask` if Flask is
there, `opentelemetry-instrumentation-psycopg` if `psycopg` is there. Run it
again when you add a dependency you want traced.

In a Docker build, pin the result rather than running `-a install` at image
build time, so an image rebuild cannot quietly add or drop instrumentation.
`opentelemetry-bootstrap -a requirements` prints the list instead of installing
it.

### 3. Configure it

```
OTEL_SERVICE_NAME=checkout
OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.nexovue.com
OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_xxxxxxxxxxxxxxxxxxxxxx
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
```

### 4. Start through the launcher

```bash
opentelemetry-instrument python app.py
```

The launcher wraps whatever command follows it, so it goes in front of your
existing start command rather than replacing it:

```bash
opentelemetry-instrument gunicorn -w 4 -k uvicorn.workers.UvicornWorker app:api
opentelemetry-instrument uvicorn app:api --host 0.0.0.0 --port 8000
opentelemetry-instrument celery -A tasks worker
```

## Gunicorn, uWSGI and anything else that forks

This is the one Python-specific failure worth knowing about in advance. A
pre-fork server initialises in the parent process and then forks workers, and
the OpenTelemetry SDK's background exporter thread does not survive a fork: the
parent's thread is not there in the child, so spans are created and never sent.

`opentelemetry-instrument` handles this for Gunicorn — it is what the launcher
exists for. If you initialise the SDK yourself instead, do it in the
`post_fork` hook so each worker gets its own provider and its own exporter
thread:

```python
# gunicorn.conf.py
def post_fork(server, worker):
    from opentelemetry.instrumentation.auto_instrumentation import sitecustomize  # noqa: F401
```

The symptom when this is wrong is specific and worth recognising: everything
looks configured, the key reads "Never used" in the console, and nothing is in
the logs to say why.

## Your own spans

```python
from opentelemetry import trace

tracer = trace.get_tracer("checkout")


def settle(order):
    with tracer.start_as_current_span("settle_order") as span:
        span.set_attribute("order.id", order.id)
        try:
            return charge_card(order)
        except Exception as error:
            span.record_exception(error)
            span.set_status(trace.Status(trace.StatusCode.ERROR))
            raise
```

The context manager ends the span for you, including on the exception path. A
span marked with an error status is one nexovue keeps whatever the sampling rate
says.

## Logs

Python's OpenTelemetry logging support attaches a handler to the standard
`logging` module, so your existing `logger.info(...)` calls become OTLP log
records with the current trace id attached. The launcher enables it when you ask:

```
OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED=true
```

The trace id is what makes the logs worth sending: it is what lets a slow trace
in nexovue show you the log lines that particular request wrote.

## Common mistakes

**Spans from a script never arrive.** A short script exits before the batch
processor's interval elapses. Call `trace.get_tracer_provider().shutdown()`
before exiting, which flushes.

**`opentelemetry-bootstrap` installed nothing.** It looks at the environment it
is run in. In a virtualenv, run it inside the virtualenv; in a container, run it
in the build stage that installed the dependencies.

**AWS Lambda.** The forking problem does not apply, but the freeze-between-
invocations problem does. See [aws-lambda.md](../platforms/aws-lambda.md).
