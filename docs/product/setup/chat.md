# Ask: chat about your telemetry

**Ask** is a panel beside every console screen. A person types a question ("What is failing right
now?"), and nexovue's chat answers from your own telemetry, findings, runbooks and case notes. It
shows each read it made as a step you can open, and labels each sentence by where it came from.

Chat has no tool that can write. nexovue checks each answer and removes model-written links and
commands; that check can miss things. Log text goes to the AI provider you choose.

Ask is in self-hosted installs only. It is off until an owner agrees to what it sends and turns it
on.

## Before you start

- **An AI connection.** Chat uses the connection set up for finding wording: your own provider
  account, or a model on your own network. Set it up first: [ai.md](ai.md).
- **The AI container running.** `scheduler-insight` answers questions. An install started with
  `SCHEDULER_INSIGHT_REPLICAS=0` shows no Ask button.

## What is sent, and to whom

Everything chat sends goes to the provider of your AI connection, and nowhere else.

- **Facts only:** the question, the conversation's earlier questions and checked answers, and
  what each read returned as numbers, fixed lists and names that pass a strict pattern. No log,
  trace or error text, and no runbook or note.
- **Facts and text:** all of that, plus log lines, error messages and trace names, and the
  runbooks and notes a question finds. **Anyone who can reach your services writes log text.**

Before any text is sent, chat masks Bearer and Basic values, passwords in URLs, `password=`-style
pairs and, unless an admin turns it off, email addresses. Masking catches some credentials, not
all: a password typed into a log line's text may not be caught. Names and customer ids stay.

**Read the exact request before you agree.** Settings → AI wording → Chat → *Show what would be
sent* shows it, built from your own data where there is some.

## Turning it on

1. Settings → **AI wording** → **Chat**.
2. An owner chooses the data level, reads *Show what would be sent*, and clicks **Agree to send
   this**. Changing the provider's address, key, region or format, or raising the data level,
   needs an owner to agree again.
3. Turn chat on. Members, admins and owners can then ask; viewers cannot.
4. Set the daily limit: how many questions each person may ask a day, 50 unless you change it,
   at most 1,000.

Each question takes up to 8 reads, each covering at most 7 days. A person has one question
running at a time.

## Runbooks

Settings → **Runbooks** holds your team's own instructions, per project, for chat to find and
quote. Members and above add, upload (Markdown) and edit them; a runbook is at most 32 KiB, and a
project holds at most 200.

- **No sync.** A runbook is a copy. Nothing is read from a wiki or a repository, and a change
  there does not reach nexovue: edit it here.
- **Do not paste secrets.** With chat on, a runbook's text may be sent to your provider. Text
  that looks like a credential needs a second click to save.
- Chat quotes a runbook as your team's words, with its age, and never as a fact it read.

## A model on your own network

Chat runs with a model on your own network, as finding wording does: list its address and port in
`AI_PRIVATE_ENDPOINTS`, and bind the model server to that private address, not to loopback
([ai.md](ai.md)).

**Ollama: set the context length.** Ollama's default context depends on the machine's GPU memory,
as little as 4K tokens, and its OpenAI-style endpoint cannot change it per request. Chat's tools
and results alone pass 4K, so set it on the server:

```bash
OLLAMA_CONTEXT_LENGTH=32768 ollama serve
```

32K is the figure the first measured run uses; it changes if that run's largest request needs
more. **The connection test does not yet check the context length**, so a model left at its
default can lose the start of each question without saying so: set it before you turn chat on.

**Models measured with chat:** none yet. Each model is listed here, with the machine it ran on and
its results, once its run is recorded.

## Who can read a conversation

A conversation belongs to the person who asked. Admins and nexovue's support staff do not read it
in the console. **An operator with access to your install's database can.** Conversations expire
with your project's retention, as findings do. Your provider keeps what it was sent under its own
terms, which nexovue cannot reach: read them.

## The MCP server is separate

Chat's switch does not affect the MCP server ([api.md](api.md)). Your own AI tools reach that with
API tokens your team made, and read only what those tokens may.
