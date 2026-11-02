# Phase 2 customers: what small teams want, distrust and refuse (2026-11-02)

> **Internal research, not a comparison or a claim.** Written 2026-11-02 from public sources read that day. Rivals are named here only to inform design; nothing in this file may be quoted in sales or marketing (Phase 2 foundation, *Founder decisions*).

> **Status:** Proposed. Research input for the Phase 2 feature documents (F28, F3, F22, F4, F5, F19, F20).
> Nothing here is approved, and it reopens no founder decision in
> [`phase-2-plan.md`](../product/phase-2-plan.md).
>
> **The main limit:** nobody was interviewed. Most of what is published comes from companies selling these
> tools. Reddit could not be searched, so first-hand voices are a few Hacker News comments, and those are
> anecdotes. **No source measured 5 to 50 engineer companies on their own.** This shows what to design against,
> not what buyers will pay for.

## How to read the evidence

| Grade | Meaning |
|---|---|
| **A** | Academic paper or benchmark, with its method published. Some are preprints; the author's interest is noted |
| **B** | Vendor's own survey or documentation. Good for "what the product does", weak for "what buyers think" |
| **C** | Vendor marketing, competitor comparison, single comment or essay. A signal only |

Web pages were read through a summarising tool, or only as search results where the [source list](#sources) says
so. **Check any quote or number against the original before it appears in anything public.**

## The seven findings that should change the documents

1. **Do not promise a root cause. Promise evidence.** In the most recent independent-looking test found, the best
   of five frontier AI agents found the right cause in 25.3% of medium tasks and 10.0% of hard ones (ORCA-bench, A,
   on a demo system). Vendors claim 82% (Traversal) and 94.5% (Sentry). These are different tests and cannot be
   compared, but the distance between them is the trust problem.
2. **A confident wrong answer is the failure that matters, so "not enough evidence" has to be a real outcome.**
   Evaluation guides, including vendors', treat a tool that always names a cause as guessing (B, C).
3. **A healthy system is a test too.** In one small benchmark, two of four AI setups reported a problem on 67% of
   questions about a healthy system (Causely, A, vendor-authored, only 3 such questions per setup).
4. **A person approving a fix does not stop a poisoned suggestion by itself.** The strongest published attack does
   not divert the agent. It feeds it a believable false cause and a matching "fix" (AIOpsDoom, A, 89.2% average
   success in 120 trials). A reviewer sees a sensible-looking proposal.
5. **Existing masking is not an injection defense.** nexovue's masking removes secrets. It has six rule families:
   credential keys, authorization headers, cookies, card numbers, authorization credentials and URL credentials
   (`apps/workers/internal/pipeline/mask/rules.go:26-38`, listed again at
   `apps/api/src/nexovue_api/projects/config.py:37-44`). Attacker text in a log message or a URL path passes through
   untouched by design. Stage 2 needs a separate layer for this.
6. **Safety must not depend on which model the customer picks.** Under one identical test, the rate at which an
   attack took over the model ranged from 0% to 86.2% (LogJack, A). The founder's decision lets the customer choose
   their model, so the fence has to be in nexovue's design.
7. **"So your data stays with you" is only true with a local model.** If the customer uses their own AI account,
   telemetry goes to that provider. The plan's own safety section says so. The selling line needs a condition
   (*Founder decisions needed*, 1).

## What on-call engineers and CTOs say, by capability

### AI root cause (F28, F3)

**They want**

- **To see the reasoning.** 95% call it important to some degree, 21.6% "critical" (Grafana survey 2026, B, 1,363
  responses, promoted by Grafana to its own audience, all company sizes).
- **Less setting up.** The top barrier to using AI on critical work is "too much manual input of required context"
  (same survey). nexovue already stores deploys, errors and the service map, so it can supply that context without
  asking. This is the strongest fit between what people ask for and what exists.
- **A cause labelled as a hypothesis, with what would disprove it** (Rootly guide, C).
- **Detection above all.** 92% see value in AI surfacing anomalies, the highest of the survey's AI uses as reported
  (B). Stage 1 is aimed at the right thing.

**They distrust**

- **Fluent wrong answers.** "A confident but wrong root cause can be worse than no AI", because on-call engineers
  tend to follow it (dev.to practitioner post, C). Traversal names hallucinated causes and automation bias, meaning
  people over-trusting a fluent answer, as the main risks, without data (C).
- **Marketing numbers.** Comparison pieces note that Rootly publishes no precision or recall (written by
  incident.io, its competitor, C). OpenObserve says accuracy "improves with every incident" and gives no figure (B).
- **The tool's reason to exist.** One commenter asks "why wouldn't I just use Claude instead of an AI SRE tool?"
  ([HN](https://news.ycombinator.com/item?id=50009404), C). The honest answer is what nexovue can do and a generic
  chat cannot: claims joined to stored facts, available the minute a finding opens, with permissions applied.
- **The gap between bosses and on-call.** 60% of directors say AI reduced toil, against 38% of individual
  contributors. Across everyone: 49% less, 35% no change, 16% more (Catchpoint/LogicMonitor SRE Report 2026, B, 400+
  respondents). The person who buys and the person woken at 3 a.m. may disagree. Write for the second one.

**Design lessons from people who built it**

- More data can make the answer worse. Datadog reports a correct signal being overridden by unrelated upstream
  errors when many tool results were summarised together. It moved to forming a hypothesis and testing it with
  narrow queries (B).
- Giving the AI source code raised accuracy by 9 to 16 points and lowered hallucination in ORCA-bench (A). That is
  an argument for sharing the GitHub connection between F3 and F5, with read access only on the reading side.
- Chained AI steps multiply errors: five steps at 90% each give about 59% (Honeycomb, B, arithmetic). Fewer steps
  are better.

### Incident tracking (F22)

- **Price per person is the common complaint**, though the source is an aggregator's reading of review sites (C).
  One small-team user: "it's really hard to justify almost $50/mo per person with Pager Duty"
  ([HN, 2023](https://news.ycombinator.com/item?id=38762440), old, C).
- **Small teams want little.** "Something simple, barebones, that just works"
  ([HN, 2025](https://news.ycombinator.com/item?id=43285105), C). "On a team this small, everyone is always on-call"
  ([HN, 2019](https://news.ycombinator.com/item?id=21153213), C). One guide argues most small teams do not need
  escalation policies or incident command roles (C).
- **Free floors exist.** incident.io has a free Basic plan limited to 1 workflow, 2 integrations and 1 schedule. Team
  costs $15 to $19 per user per month. AI starts on Pro at $25, with root cause analysis as a paid add-on (its pricing
  page, B). PagerDuty's free plan covers up to 5 users (search result, C).
- **Meaning for F22.** The plan already keeps on-call with PagerDuty. A case should need no setup: no required
  roles, severities or forms, and the timeline should write itself from what nexovue already records. The sources
  also point small teams toward Slack-based tools, and the plan lists only GitHub Issues and Jira for F22. The spec
  must say plainly whether running a case from Slack is in or out.

### Chat with telemetry (F4)

- **Failures are partial, not obvious.** Datadog: a correct query with the wrong rollup interval. Another write-up:
  `@duration:>2` for "over 2 seconds", where the data is stored in nanoseconds (B, C). A wrong number looks exactly
  like a right one. **So each answer must show the query it ran and the time range it read**, not only a citation.
- **Mistrust is expected.** Splunk's own product says "The assistant can be wrong" and is read-only (B).
- **Honeycomb's model is still the conservative reference:** the AI is not connected to databases, output is parsed
  and validated, what it produces cannot destroy anything, and each user has a daily limit. It also chose not to build
  a chat screen, and its author admits a motivated attacker will still get "something funky" (B, 2023).
- **Tenant isolation is a buyer's first question.** One commenter asked a vendor how it prevents "cross-contamination
  or data leakage across customers", and how an AI SRE is kept from straying from its task
  ([HN](https://news.ycombinator.com/item?id=47337764), C).
- **Measure by flagged wrong answers** rather than usage (dev.to on text-to-SQL, C).

### AI fixes (F5)

- **Nobody asks for unreviewed writes.** One vendor's interviews found no on-call engineer wanted an agent with
  unreviewed write access to production (Xurrent, C, search result). Autonomous action drew the most skepticism in
  Grafana's survey: 77% see value, 15% do not trust it yet (B).
- **Review load is the less obvious cost.** Developers complain of "an endless stream of AI slop" (paper title, A,
  which found AI pull requests recognisable by style and eroding trust). A study of agent pull requests reports that
  many rejections followed slow reviews or repeated change requests, and that correctness benchmarks miss integration
  work (A). A secondhand report puts 38% of developers saying AI code needs more review than a colleague's (Sonar via
  a blog, C).
- **Meaning for F5.** A small team has one reviewer, who is also on call. Default to a suggestion on the finding.
  Make the draft pull request an explicit click, small, and state what evidence led to it and what was not checked.
  Refuse to produce one when evidence is thin. **Nothing was found measuring acceptance of AI fixes for production
  incidents.** Any acceptance target in F5 is a guess.

### SLOs (F19)

- **The failure is usually organisational.** "If budget exhaustion doesn't change next sprint's work, you don't have
  an SLO program" (DevOpsDays Rockies 2026 talk, C). "I'm yet to see an error budget effectively inform eng decision
  making" ([HN, 2023](https://news.ycombinator.com/item?id=38051172), C). Others report it working at Google and, area
  by area, at Uber ([HN](https://news.ycombinator.com/item?id=23704578), C). Those are much larger companies.
- **Too-tight targets train people to ignore them.** Set them slightly below what the service already achieves (C).
- **Keep it few.** One guide suggests two or three SLOs for a three-person team, each tied to revenue (C).
- **Alert on burn rate, using two time windows.** This means alerting on how fast the allowed failures are being
  used up, rather than after the target has already been missed (C, standard Google SRE Workbook practice).
- **Meaning for F19.** Propose a target from the service's own history, which rollups make possible. Limit the screen
  to a few. **An SLO alert must not become a second alarm for a problem F6 already reported**: join the existing finding.
  Production use is low (29% of SREs and 18% of developers in Grafana 2025, per our
  [earlier research](phase-2-scope-2026-10-30.md)). The evidence that SLOs win a purchase remains absent.

### Dashboards (F20)

- **Complaints are about value and upkeep, not missing features.** "Building useful dashboards is complicated no matter
  what tool you use" ([HN, 2023](https://news.ycombinator.com/item?id=37640638), C). An essay titled "I don't want more
  dashboards, I want answers" describes sprawl and uncertainty over which is authoritative (C). No survey on unused
  dashboards was found.
- **Roughly 9 in 10 see value in AI generating dashboards, alerts and queries** (Grafana, B). Seeing value is not
  paying for it.
- **Meaning for F20.** It is expected rather than a reason to buy (weak evidence, as the earlier research said).
  Every dashboard should show when it was last viewed and start from a service, so it cannot compete with the
  findings-first home screen.

### Deploy tracking (F3, existing deploy events)

- **The most agreed advice in this whole search:** without markers, you are guessing whether a change came from a
  deploy or traffic. Group errors by version. Offer a "what changed since the last deploy" view (all vendor guides, C,
  no complaint data).
- **The weak spot is coverage.** A team that does not send deploy events gets a worse explanation. The explanation
  must say "no deploy information for this service" rather than imply that no deploy happened. That is a plain
  correctness rule, and an easy place to mislead.

## Why teams turn AI off

**No direct evidence was found.** No survey, and no report of a team switching off Datadog's Bits AI. One Traversal post
mentions teams turning off AI runbook assistants, with no source or count. What follows is inference from nearby
evidence, ranked by how well supported it is:

| Likely reason | Support | What the documents should do |
|---|---|---|
| Wrong or useless answers wasting time at the worst moment | ORCA-bench, Causely, guides treating false positives as the trust-killer | Abstain; report rate of "wrong and confident"; thumbs-down on each explanation |
| Unpredictable cost | Cost is the top selection criterion, 65% (Grafana, B); usage billing complaints (C) | A hard monthly limit, a visible count, and clean behaviour when it is reached |
| Legal or privacy refusal | Honeycomb: some customers "will not want their data, even if it's just metadata, involved in a machine learning model" (B) | Off by default; show exactly what is sent; a preview before enabling |
| Too much noise and text | Signal-to-noise is a top-three concern at 34% (Grafana, B) | Short output; no separate AI notification |
| Set-up effort | "Too much manual input of required context" (Grafana, B) | Works from what nexovue already holds |

## Trust, cost and data location objections

### Cost: what the market charges

| Product | AI price | Grade |
|---|---|---|
| Datadog Bits Investigation | AI Credits: $500 for 500 a month, $1.30 on demand. Replaced $500 per 20 investigations around June 2026. One read of Datadog's page gave about 4 credits per investigation; other sources say 6.5. **By our arithmetic, roughly $4 to $8.45 each. Datadog publishes no flat price, and this is unconfirmed** | B/C |
| Sentry Seer | $40 per active contributor a month, unlimited, from January 2026 | B, search result |
| OpenObserve | AI SRE in Enterprise, labelled Preview. Its cloud uses AI Credits at $0.50 each | B |
| incident.io | Pro and up; root cause analysis is a paid add-on | B |

**For nexovue:** the founder decided nexovue runs no AI service for self-hosters, so the bill is the customer's. No
token cost for an investigation was measured; **any figure in F28 is a guess until the Stage 2 test run measures it.**
Honeycomb's reported latency of 2 to 15+ seconds is old and for a smaller job.

### Self-hosting buyers

- **Competitor position, from their own pages. This answers much of open question 3 in the plan, but check again
  before publishing:**
  - **SigNoz:** Noz is "Available to SigNoz Cloud users". Self-hosters can run an MCP server for their own coding
    agents. Enterprise self-hosted or bring-your-own-cloud is offered to those who cannot send data out.
  - **Grafana:** self-managed Grafana connects to a Grafana Cloud Assistant backend. The page does not confirm
    whether it works without that link, and mentions no bring-your-own model.
  - **OpenObserve:** runs its AI SRE with bring-your-own keys and any OpenAI-compatible endpoint, including self-hosted
    models, but in Enterprise, in Preview. The page says the assistant is free under 200 GB a day; this came from a
    search result. Its pages disagree on the provider list.
- **So nexovue's distinct position, if it is built:** free, AGPL, open source, with AI on the customer's model and not
  behind an enterprise plan, and fenced by design. It is narrow, and OpenObserve is close. The "not unique" risk in the
  plan stands.
- **A local model has real costs.** The plan already records hardware beyond 16 GB and weaker quality. LogJack's results
  for models commonly self-hosted vary widely: Llama 3.3 70B 86.2%, Qwen3 Coder Next 33.1%, Devstral 2 15.6%, Kimi K2.5
  11.9%. Those are specific models under a harsh setup, not a verdict on local AI, and the small models a 16 GB machine
  could run were not tested.

### India-based buyers

**This is not legal advice, and most of it is secondary sources.** Have a lawyer read the primary text before any claim.

- **DPDP Act (India's personal data law):** Rules notified in November 2025. Core duties phase in after 18 months,
  so around May 2027. Section 16 and Rule 15 on transfers had reportedly not commenced as of September 2026.
- **It is not a blanket "keep it in India" rule.** Transfers are allowed except to countries the government restricts,
  though sources warn that this could become close to localisation. A customer remains responsible for its processors,
  and security safeguards include keeping logs for a year.
- **Telemetry can hold personal data, such as IPs and user IDs.** No guidance was found on how observability data is
  treated. Treat this as unknown.
- **Regulated sectors are stricter than the DPDP Act.** RBI's payment-data rule reportedly keeps analytics and AI
  processing of payment data in India (secondary). SEBI's cloud framework reportedly requires logs of regulated entities
  to reside in India, while SEBI put localisation under its cybersecurity framework (CSCRF) in abeyance on 31 December
  2024. **The current position needs checking.** MediaNama's headline says regulated entities are responsible for data
  privacy in AI use; the article was not read.
- **A new route exists.** Claude can now run inside India through Amazon Bedrock, keeping requests between Mumbai and
  Hyderabad (AWS and Anthropic, per press, October 2026). Bedrock also offers OpenAI models in India; details were not
  confirmed. Residency is not compliance by itself. **So the "their own AI account" option must accept a regional endpoint**,
  and the settings screen should tell the customer which region their chosen endpoint uses when nexovue can know it.
- **Money:** no named Indian company's own migration story with rupee figures was found. The only rupee figure, a
  ₹10.6 lakh monthly median Datadog spend at Series B Indian SaaS companies, comes from a vendor's post and could not be
  verified. Do not use it.
- **Self-hosting helps but does not settle it:** one source notes that a transfer review looks at who can access the
  data, not only the server's location. With a local model, nothing leaves. With an AI account, the customer must review
  that provider.

## How to evaluate AI operations tools: current practice

Sources broadly agree:

1. **Replay resolved incidents with known causes**, 5 to 20 of them, including one where the right answer is "not
   enough evidence" (Rootly, NeuBird, B/C).
2. **Classify each miss:** missing source, wrong context, or wrong reasoning (Rootly).
3. **Then live alerts**, with each result rated by the responder.
4. **Measure:** agreement with known causes, *honest misses* (reported as inconclusive rather than as a wrong cause),
   whether cited evidence supports the claim, time to a useful hypothesis, and zero unapproved writes (Rootly).
5. **Label against real incidents, and align any AI judge with human scoring** (Datadog, B; ORCA-bench reports κ = 0.91
   agreement between its AI judge and human re-scoring, A). κ measures agreement beyond chance.
6. **Run healthy systems as well** (Causely, A), which the plan's false-alarm run already provides for.
7. **Be careful with synthetic tests:** Traversal argues production data beats staging scenarios (C, self-interested).
   ORCA-bench says real systems remain untested.

**A common target is 80% precision** (vendor guides, C). Given ORCA-bench's results, **do not adopt it as a number for
nexovue before measuring.** A better first gate counts what the design controls: every claim links to data, abstention
works, no write occurs, and the injection suite passes. Accuracy is reported as measured, then the founder sets a bar,
as already happens for false alarms.

**Public benchmarks to reuse:** the OpenTelemetry Demo is also ORCA-bench's and Causely's kind of test bed, so results
are roughly comparable in form. [OpenRCA](https://microsoft.github.io/OpenRCA) (335 failures, 68 GB, Microsoft) and
AIOpsLab are available. OpenRCA's headline score was not confirmed in this pass.

## Defending against prompt injection through telemetry

**Prompt injection** means text the AI reads being treated as instructions. Through telemetry, anyone who can make a
customer's public service log something can reach the AI, with no account. LogJack states this directly, and Straiker's
demonstration used one POST to `/login` with a fake telemetry record in the username field (C, it sells AI security, and
that agent had write access).

| What the evidence shows | Source | Consequence |
|---|---|---|
| Filters that look for injection mostly fail on logs: Azure 1/32, GCP 0/32, ProtectAI 1/32. Bedrock does not inspect tool results. Log formatting makes attacks look like operational text | LogJack, A | Never count a filter as the defense |
| Filters also fail against disguised causes: 85 to 100% evasion | AIOpsDoom, A | Same |
| Noticing an attack is not safety. The two Claude models flagged 72 to 80% of attacks yet still ran dangerous commands in 16 to 19% of trials. Those were commands they generated themselves, not the injected ones, so the text still changed what they did | LogJack, A | Do not rely on the model's warning |
| "Sanitize and execute": a model removed the bad URL and ran the rest | LogJack, A, one observed case | Output must be checked outside the model |
| Disguise works: a fake "SOP-2847" reference beat GPT-4o where an obvious attack failed | LogJack, A | Test suite needs disguised payloads |
| Replacing untrusted fields beats reading them. AIOpsShield finds attacker-reachable fields using a canary (a marker string sent through the service), and masks them before the AI sees them | AIOpsDoom, A. Figures for it were not read; depends on telemetry being structured | The closest match to the plan's "sanitized or replaced". **Free-text log bodies are its weak case** |
| After reading untrusted input, the agent must be unable to trigger consequential actions. Six patterns, including Dual LLM (a planner that never sees raw data and a reader with no tools) and Map-Reduce | Design Patterns paper, A | The structure for F3, F4 and F5 |
| No more than two of: reads untrusted input; reaches private data; changes state or communicates out | Meta Rule of Two, B. Willison's "lethal trifecta" is the same idea, C | See below |
| Human approval can fail if someone "blindly confirms" | Meta | Approval is needed but is no boundary |

**Applying the Rule of Two to nexovue** (our reading, for the designs to test):

- **F3 and F4 hold two by design:** they read untrusted text and private telemetry. So they **must have no way out.**
  **Our inference, not from a source:** a link or image in AI text rendered by the console is a way out, because the
  browser fetches it and the URL can carry data. Answers must render as plain text plus links nexovue itself built.
- **F5 is where it breaks.** Untrusted telemetry, a private repository and opening a pull request are all three. The
  part that writes must never read raw telemetry. It takes structured, validated facts from the reader, and the pull
  request itself must not carry raw attacker text, since a reviewer's own AI tools may read it.
- **False causes remain possible with no permissions at all.** An attacker can still push the explanation toward a wrong
  answer, which costs an on-call engineer time. The defense is that facts come from stored rollups and deploy records,
  which an outsider cannot write, and that any claim resting on free text is labelled that way.

**Test suite for the gate.** LogJack's benchmark is public: 42 payloads, including 10 harmless controls that catch
over-blocking. **It must be adapted, since it targets AWS logs, and run on every supported AI option.** Add AIOpsDoom's
false-cause form, payloads in nexovue's own fields, and attempts to leave through rendered output. Its authors warn that
their results are an upper bound for read-only agents and that each payload ran only five times.

## Decisions taken

Recommended by the authors, founder may overrule.

| # | Question | Options | Recommendation and why |
|---|---|---|---|
| R1 | What does the explanation promise? | "Root cause" / "likely cause with evidence" | **Likely cause with evidence, and "not enough evidence" as a valid result.** Independent accuracy is low, and the plan already requires causes labelled as likely |
| R2 | Evaluation gate for AI quality | Fixed accuracy bar now / measure first | **Measure first**, with hard gates on evidence links, abstention, no writes and injection. A number chosen now would be invented |
| R3 | Main injection defense | Input filter / model choice / structure | **Structure:** reader without tools, plain-text output, stored facts first. Field replacement is added and filters may only be extra |
| R4 | Does F5's writer see telemetry? | Yes / structured facts only | **Structured facts only**, otherwise it breaks the Rule of Two |
| R5 | AI spending control | None, the bill is the customer's / a hard limit | **Hard limit, required when AI is turned on.** Cost leads buyers' criteria, and a finding must survive the limit |
| R6 | F4 answers | Citation only / show query and time range | **Show both.** Wrong queries look right |
| R7 | SLO alerts | Separate / join the finding | **Join the finding.** Noise is a top concern |
| R8 | Case setup | Required fields / none | **None required.** Small teams want simple |
| R9 | AI endpoint | Fixed provider list / any compatible endpoint with regional choice | **Any compatible endpoint.** It serves India-bound buyers and the founder's two options, and each one still has to pass the test suite before being called supported |

## Founder decisions needed

1. **The selling line.** "All on your own server, so your data stays with you" is false when a customer uses an AI
   account. **Recommend:** "Your telemetry stays on your server. AI is off until you choose it, and you can run it on your
   own model." It is your approved wording, and it is what is sold, so it is your call.
2. **Talk to buyers before F5 and F20 are built.** This research cannot say what makes a small team refuse a tool, the
   same gap as last time. **Recommend** five to eight conversations with on-call engineers and CTOs at small Indian SaaS
   companies, ideally before F28's spec is approved. It costs your time and nothing else.
3. **A lawyer's reading before any India compliance claim.** **Recommend** no public claim on DPDP, RBI or SEBI until
   that is done. It would cost real money, and is unpriced; until then, state only the facts of where data goes.

*AI on the paid hosted service* (plan, open question 1) remains the founder's, before hosting opens. Nothing found here
changes it. Hosted nexovue calling an AI provider makes nexovue a processor of telemetry going to that provider, which
question 3's lawyer should cover.

## What this did not find

- What makes a 5 to 50 engineer company refuse or pay for a tool. It remains unanswered after a fourth pass.
- Direct evidence of why AI features get turned off.
- Reddit: not accessible. Hacker News searches gave few on-topic comments, some several years old, and most AI SRE
  mentions there come from sellers.
- Acceptance rates for AI fixes to production incidents.
- How logs are treated under the DPDP Act.
- Grafana's data-handling page: the address tried returned not found, so its model providers are unconfirmed.
- OpenRCA's headline score; AIOpsShield's own figures; the Sonar and Lightrun originals; a DORA or METR primary source.

## Sources

**Academic and benchmarks**

- [LogJack](https://arxiv.org/html/2604.15368): preprint; agents had shell tools, so an upper bound
- [When AIOps become "AI Oops" (AIOpsDoom, AIOpsShield)](https://arxiv.org/html/2508.06394v1): first 100,000 characters read
- [ORCA-bench](https://www.alphaxiv.org/abs/2607.28545): 1,079 tasks on a demo system; its authors note real systems are untested
- [Causely benchmark](https://arxiv.org/html/2605.18327): authors use causely.ai email addresses; 72 runs, no significance tests
- [Stalled, Biased, and Confused](https://arxiv.org/abs/2601.22208): abstract only
- [Design Patterns for Securing LLM Agents](https://arxiv.org/html/2506.08837v3)
- ["An Endless Stream of AI Slop"](https://arxiv.org/pdf/2603.27249): search result
- [Agentic pull requests on GitHub](https://arxiv.org/pdf/2509.14745): search result
- [OpenRCA](https://microsoft.github.io/OpenRCA): search result for its size

**Surveys, all published by sellers**

- [Grafana Observability Survey 2026](https://grafana.com/observability-survey/2026/)
- [SRE Report 2026, press release](https://www.businesswire.com/news/home/20260122462526/en), [Catchpoint on AI and toil](https://www.catchpoint.com/blog/sre-report-2026-ai-optimism-and-the-economics-of-effort), [role split](https://www.logicmonitor.com/resources/2026-observability-ai-trends-outlook-2): search results

**Vendor documentation and engineering accounts**

- [Datadog: building Bits AI SRE](https://www.datadoghq.com/ja/blog/building-bits-ai-sre/), [Datadog pricing](https://www.datadoghq.com/pricing/?product=bits-ai-sre), [Datadog: natural-language query evaluation](https://www.datadoghq.com/blog/llm-observability-at-datadog-nlq.md)
- [Honeycomb: the hard stuff with LLMs](https://www.honeycomb.io/blog/hard-stuff-nobody-talks-about-llm), 2023
- [Meta: Agents Rule of Two](https://ai.meta.com/blog/practical-ai-agent-security/)
- [SigNoz: Noz and AI tools](https://signoz.io/docs/ai/overview/), [Grafana Assistant](https://grafana.com/docs/grafana-cloud/machine-learning/assistant/), [OpenObserve AI SRE](https://openobserve.ai/ai-sre/), [OpenObserve agent setup](https://openobserve.ai/docs/enterprise-setup/sre-agent/), [OpenObserve assistant](https://openobserve.ai/ai-assistant/)
- [incident.io pricing](https://incident.io/pricing), [Sentry Seer availability](https://sentry.io/changelog/seer-sentrys-ai-debugger-is-generally-available), [Sentry Seer price announcement](https://www.businesswire.com/news/home/20260127739891/en)
- [Splunk AI assistant](https://agent-observability-docs.splunk.com/concepts/ai-assistant)

**Vendor guides and opinion (self-interested)**

- [Rootly: evaluate AI in incident management](https://rootly.com/ai-sre-guide/evaluate-ai-incident-management.md), [NeuBird: evaluation guide](https://neubird.ai/blog/ai-sre-evaluation-guide), [incident.io: accuracy testing](https://incident.io/blog/ai-root-cause-analysis-accuracy-testing-guide), [incident.io on Rootly](https://incident.io/blog/rootly-ai-accuracy-vs-competitors)
- [Traversal: state of the field](https://www.traversal.com/blog/ai-in-incident-response-state-of-the-field-2026-sre), [Traversal: evaluating](https://www.traversal.com/blog/how-should-you-evaluate-an-ai-sre-product), [Xurrent guide](https://www.xurrent.com/blog/ai-sre-guide)
- [Dash0 on Bits AI](https://www.dash0.com/comparisons/datadog-bits-ai-alternatives) (a competitor), [pricing change tracker](https://pricingsaas.com/companies/datadog.network-monitoring/diffs/2026W24)
- [Straiker: AI SRE injection](https://www.straiker.ai/blog/ai-sre-agent-prompt-injection-kubernetes-ransomware)
- [Why LLMs can't replace your SREs yet](https://dev.to/david_aronchick_ea415de50/why-llms-cant-replace-your-sres-yet-40e0), [natural-language log search](https://dev.to/carolemlago/a-natural-language-interface-for-datadog-log-search-4occ), [text-to-SQL observability](https://dev.to/bagofwords/building-reliable-ai-analysts-an-observability-framework-for-text-to-sql-systems-25ln)
- [Willison: the lethal trifecta](https://simonwillison.net/2025/jun/16/the-lethal-trifecta/)
- [DevOpsDays Rockies 2026](https://talks.devopsdays.org/dodroxrox26/talk/FDTEUK/), [SLOs for a 3-person team](https://stackharbor.com/en/knowledge-base/slo-design-3-person-saas-team/), [Lightstep: what made SLOs messy](https://medium.com/lightstephq/what-made-slos-so-messy-and-what-we-can-do-about-it-89be415a80b3)
- [I don't want more dashboards](https://falcao.org/posts/i-dont-want-more-dashboards-i-want-answers/), [deployment impact dashboards](https://oneuptime.com/blog/post/2026-02-06-deployment-impact-dashboard-releases-otel-metrics/view)
- [PagerDuty alternatives](https://nurbak-website.pages.dev/en/blog/pagerduty-alternatives), [Rootly vs incident.io reviews](https://toolquestor.com/vs/rootly-vs-incidentio)

**India (secondary unless noted; none is legal advice)**

- [DPDP Rules 2025, India Briefing](https://www.india-briefing.com/news/dpdp-rules-2025-india-data-protection-law-compliance-40769.html), [cross-border rules and timing, Rainmaker](https://rainmaker.co.in/indias-dpdp-act-2023-rules-2025-cross-border-data-transfer-rules-negative-list-risks-compliance-action-plan-for-indian-businesses/), [transfers, Vidhi](https://vidhilegalpolicy.in/blog/cross-border-data-transfers-and-data-localization-mandate-under-the-data-protection-regime/)
- [SEBI holds off on CSCRF localisation, MediaNama](https://www.medianama.com/2025/01/223-sebi-holds-off-on-data-localisation-cscrf-framework/), [SEBI cloud framework, Tata Communications](https://www.tatacommunications.com/blog/sebi-cloud-adoption-framework-key-takeaways), [RBI and sector rules reference](https://cf-6cy.pages.dev/guides/topic_xborder_india.html)
- [Claude in India via Bedrock, CRN Asia](https://www.crnasia.com/india/news/2026/anthropic-takes-claude-inference-live-in-india-opens-path-to-regulated-ai-deployments), [AWS notice, aws-news](https://aws-news.com/article/2026-09-30-amazon-bedrock-expands-claude-model-availability-to-in-country-inferencing-in-india)
- [Indian mid-market cost post](https://dev.to/aicloudstrategist/datadog-alternatives-for-indian-mid-market-cost-aware-2p7k) (vendor; figure not used)

**First-hand comments (Hacker News; anecdotes)**

- [50009404](https://news.ycombinator.com/item?id=50009404), [47337764](https://news.ycombinator.com/item?id=47337764), [38762440](https://news.ycombinator.com/item?id=38762440), [43285105](https://news.ycombinator.com/item?id=43285105), [21153213](https://news.ycombinator.com/item?id=21153213), [38051172](https://news.ycombinator.com/item?id=38051172), [23704578](https://news.ycombinator.com/item?id=23704578), [37640638](https://news.ycombinator.com/item?id=37640638)
