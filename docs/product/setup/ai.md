# AI wording on a finding

Every finding page lists the facts that line up with it: a deploy just before, a slower call to another
service, a new error, a failed check. Nexovue works those out with ordinary code, and they are there
whether or not you ever turn AI on.

AI wording is one optional extra on top. It puts those same facts into plainer sentences, each linked to the
fact it rests on, labelled with the model that wrote it. **It is off until an owner and an admin turn it on,
and it never decides what the likely cause is.** Code does that, from the facts.

This page is for whoever sets it up. Nothing here applies on nexovue's hosted service, which does not offer AI
wording yet.

## What is sent

One request per finding version, to the address you saved. It holds:

- the finding's stand-in name for its service, what moved (slower, more errors), its severity, the usual and
  the worst reading, and how many minutes ago it began;
- each fact's kind, how Nexovue knows it (a signed deploy record, counted from telemetry, read from telemetry
  text), the numbers in it (before, during, minutes from the start, deploys in the last seven days, the chance
  that a deploy that close to a finding is a coincidence), and stand-in names for the services, versions, errors
  and checks it names;
- which fact the rules chose as the likely place to look, if any, and the next checks Nexovue offers.

It never holds your real names (a service reads as `service_1` and is turned back into its name on your page,
after the answer comes back), log lines, error messages, trace contents, host names, user data, or any code.

**You can read the exact request before anything is sent.** `/settings/ai` shows it for a sample, and for
any past run, and says whether it can still be rebuilt byte for byte.

## Setting it up

1. **Run the AI container.** A self-hosted install starts `scheduler-insight` by default. It holds the AI key
   and nothing else, and reaches only the database and, for chat, the API.
2. **Give the install an AI key.** `nexovue install` writes `AI_SECRET_KEY` for a new install; an older one
   gets it from `nexovue upgrade`. It seals your provider key. Without it `/settings/ai` says AI is
   unavailable, and nothing else changes.
3. **An admin saves the connection** on `/settings/ai`: the provider's address, the model, the key, and a
   monthly token limit. A limit is required: nothing is sent while there is none.
4. **An admin tests it.** The test is a real call, with nothing from your telemetry in it, and it counts to
   the month.
5. **An owner agrees** to what is sent, to that address. Changing the address, the format or the key ends the
   agreement, and AI turns itself off until an owner agrees again.
6. **An admin switches it on.** A project can be kept out of AI wording on the same screen.

## What the switch does not cover

- **The MCP server.** An AI assistant that reads your data with your own API tokens is separate. This switch
  does not affect it.
- **Chat, and suggested fixes,** if you use them, have their own settings and their own agreement on the
  same screen.
- **Your provider's own rules.** Your key identifies you to the provider, and its terms decide what it keeps
  and whether it trains on what it receives. A free tier may read and keep what it is sent. Use nothing
  you would not send to it.
- **A gateway.** If the address is a gateway, the model name you choose can decide which company receives the
  facts.

## What it costs, and the limits

Nexovue counts only its own use of your key, and stops there: the limit does not see what else uses the key.
Warning findings stop at 80% of the month's limit; critical findings may use all of it. A finding may be
worded three times in 24 hours, automatic runs included, and a person who asks again is told when the next is
allowed. The suggested limit is a guess until nexovue measures a real run.

## A model on your own network (Ollama and others)

A private address is refused unless the server's operator lists it. In `nexovue.env`, on the AI container:

```
AI_PRIVATE_ENDPOINTS=192.168.1.20:11434
```

- **Address and port, comma separated.** A range is allowed down to `/16` (IPv4) or `/48` (IPv6):
  `192.168.1.0/24:11434`, `[fd00::7]:11434`. An entry with no port, one that is not wholly private, or a wider
  range stops the container starting, and the error names it.
- **Loopback is never reachable from a container.** `localhost` and `127.0.0.1` are refused. Bind the model
  server to the machine's private address instead, and list that address. (Ollama reads its bind address from
  `OLLAMA_HOST`; **nexovue has not checked that against Ollama's current documentation**, so read it there.)
- **Plain `http` is allowed only to such a listed private address,** written as the address. Every other
  provider must be `https`.
- The address is checked again when the connection is made, and a redirect is never followed.

## What the AI container can reach, and what to add

By design it can reach the database and, for chat, the API. It cannot reach the telemetry store, the message
broker or the object store. **It is not an outbound firewall.** Docker lets a container open connections to
the internet and to the machine it runs on, and nexovue's own checks stop the code nexovue wrote from going
anywhere but your provider, not a process that someone has broken into.

On a cloud server, add a rule on the host that stops containers reaching the cloud's metadata address,
`169.254.169.254`, which hands out the server's own credentials. For example, on a Linux host with Docker, a
rule in the `DOCKER-USER` chain dropping traffic from the AI network to that address. Nexovue's own test
records today that this address is **open** from the AI container, so the rule is yours to add. An optional
outbound limit for the container, to your provider only, is planned and is not built yet.

## Running with no AI container

Set `SCHEDULER_INSIGHT_REPLICAS=0` in `nexovue.env` and run `nexovue install`. Nothing else changes: findings,
facts, alerts and every screen work as before. `/settings/ai` says the container is not running and will not
switch AI on. If AI was on, turn it off there too, or its wording waits ("Plainer wording is not available
right now") and the monitor opens a ticket.

## Which providers

**None is offered yet.** Nexovue speaks three formats (OpenAI-style, Claude's Messages API, Amazon Bedrock
Converse), and a format appears on `/settings/ai` only after nexovue has completed one real call through it
and recorded the date. That has not happened for any of them. A connection may be used once its test passes, and
is called *supported* only after nexovue's own fault and injection tests, which have not run on any model.
Nexovue has not tested what a model on your own network will write, either.

## What to expect

Until nexovue has measured how often its rules name the right place to look, no finding states a likely
cause, and AI wording does not run. The page shows the facts and the links beside them, and says that no
likely cause is stated yet. That is deliberate, not a fault.
