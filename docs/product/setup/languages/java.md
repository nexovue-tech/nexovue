# Java

Java has the most complete zero-code instrumentation of any language. A single
JAR attached to the JVM instruments Spring, servlets, JDBC, JMS, Kafka clients,
HTTP clients, gRPC and around a hundred other libraries, with no change to your
application, your build or your dependencies.

## Already exporting OpenTelemetry

Set the variables and restart.

```
OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.nexovue.com
OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_xxxxxxxxxxxxxxxxxxxxxx
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
```

The protocol variable is not optional in Java. The agent's default has been gRPC
in some major versions and `http/protobuf` in others, and a gRPC exporter
pointed at an HTTP endpoint fails with an error that names neither. State it.

Every variable below has a system-property equivalent, if properties are how
your deployment is configured: `OTEL_EXPORTER_OTLP_ENDPOINT` is
`-Dotel.exporter.otlp.endpoint`, and the rest follow the same rule — lower-case,
dots for underscores, `otel.` prefix.

## Starting from nothing

### 1. Get the agent

Download `opentelemetry-javaagent.jar` from the releases of
`open-telemetry/opentelemetry-java-instrumentation` on GitHub. It is one file
with no dependencies. Put it somewhere your deployment can reach — next to the
application JAR, or baked into the image.

Pin a version rather than tracking `latest`. An agent that changes underneath a
running service changes what your telemetry looks like.

### 2. Attach it

```bash
java -javaagent:/opt/opentelemetry-javaagent.jar -jar app.jar
```

`-javaagent` has to come before `-jar`. Arguments after `-jar` go to your
application, not to the JVM.

If the start command is not yours to change — an application server, a
Dockerfile you inherited, a `CMD` you would rather not edit — use the
environment variable the JVM reads on its own:

```
JAVA_TOOL_OPTIONS=-javaagent:/opt/opentelemetry-javaagent.jar
```

The JVM prints a line to stderr saying it picked the variable up, which is a
useful confirmation that the agent is actually attached.

### 3. Configure it

```
OTEL_SERVICE_NAME=checkout
OTEL_EXPORTER_OTLP_ENDPOINT=https://ingest.nexovue.com
OTEL_EXPORTER_OTLP_HEADERS=x-nexovue-key=nvk_live_xxxxxxxxxxxxxxxxxxxxxx
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
```

### 4. Restart and look

The agent logs what it is doing at startup. If you want to see it working before
the data lands, turn its own logging up:

```
OTEL_JAVAAGENT_DEBUG=true
```

That is verbose and is for a first setup, not for a running service.

## Signals

The agent sends traces, metrics and logs. All three go to the same endpoint with
the same key, and you can turn any of them off:

```
OTEL_TRACES_EXPORTER=otlp
OTEL_METRICS_EXPORTER=otlp
OTEL_LOGS_EXPORTER=otlp
```

Setting one of these to `none` disables that signal. Metrics in particular are
worth leaving on in Java: the agent exports JVM heap, garbage collection, thread
and class-loading metrics without any work from you, and those are the readings
that explain a slow service when the traces do not.

The logs exporter picks up Logback and Log4j output and attaches the current
trace id, which is what lets a trace in nexovue show the log lines that request
wrote.

## Your own spans

The agent handles the framework boundaries. For your own code, add the API
dependency — the API only, not the SDK; the agent supplies the implementation:

```xml
<dependency>
  <groupId>io.opentelemetry</groupId>
  <artifactId>opentelemetry-api</artifactId>
</dependency>
```

The lightest way to use it is the annotation, which needs
`opentelemetry-instrumentation-annotations` as well:

```java
import io.opentelemetry.instrumentation.annotations.WithSpan;
import io.opentelemetry.instrumentation.annotations.SpanAttribute;

@WithSpan("settle_order")
public Receipt settle(@SpanAttribute("order.id") String orderId) {
    return chargeCard(orderId);
}
```

The agent turns that into a span, ends it when the method returns, and records
the exception and error status if it throws. An errored trace is one nexovue
keeps whatever the sampling rate says.

## Spring Boot without the agent

Spring Boot's own starter is an alternative to the agent, if you would rather
have the instrumentation in your dependency tree than attached to the JVM:

```xml
<dependency>
  <groupId>io.opentelemetry.instrumentation</groupId>
  <artifactId>opentelemetry-spring-boot-starter</artifactId>
</dependency>
```

It reads the same environment variables and the same `application.properties`
keys. It covers less than the agent does — it instruments Spring, not everything
on the classpath — and it needs a build change and a redeploy rather than a JVM
flag.

Use the agent unless you have a reason not to. The common reason is a deployment
where adding a file to the image is harder than adding a dependency to the POM.

## Common mistakes

**The agent is not attached.** Look for the OpenTelemetry startup lines in the
application's own stdout. If they are absent, `-javaagent` landed after `-jar`,
or the path to the JAR is wrong, and the JVM does not always complain loudly
about either.

**Connection refused or a protocol error.** `OTEL_EXPORTER_OTLP_PROTOCOL` is
unset and the agent chose gRPC. Set it to `http/protobuf`.

**The service name is the JAR name.** The agent guesses when
`OTEL_SERVICE_NAME` is unset, and its guess is rarely what you want on a screen.

**Two sets of spans for one request.** The agent and a Spring starter are both
running. Use one.
