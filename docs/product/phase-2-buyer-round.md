# Phase 2 buyer round: the interview guide

> **Status:** Guide for the founder to run. Written 2026-11-08. Nothing here is a new decision: every rule in
> *What the answers decide* was approved on 2026-11-02 in
> [`phase-2-foundation.md`](phase-2-foundation.md) (*Founder decisions*, 8, 21 and 25) or in a feature's own spec.
> Where two documents disagree, *Not checked* (the last section) says so and this guide does not choose.
>
> **Line numbers** (`file:line`) are for the repository on 2026-11-08 and will drift. Paths are relative to `docs/`.

## 1. What this round is for

Nexovue has no customers and nobody has been interviewed. Five features were designed from reading and from
public pages, and each one has a spot where the design only holds *if buyers want it*. This round is where we ask.
It is the one thing no desk research could answer (`product/phase-2-foundation.md:694`).

The founder approved **one round of five to eight conversations, not one round per feature**
(`product/phase-2-foundation.md:694`, founder decision 8). It is held **before F28's Step 1b**, and F28's Step 1b is a hard
stop until it is done (`features/deployment-release-tracking/status.md:7`). It carries every Phase 2 feature's questions:

| Feature | What it needs from the round |
|---|---|
| F28, deploy and release tracking | Whether `service.version` is set, how releases are checked, whether a release page would be used |
| F22, case management | Which tracker, whether two-way sync matters, three small defaults |
| F4, AI chatbot | Whether to build the chat panel (Steps 2 to 7) at all |
| F5, AI solution (suggested code fixes) | Whether people would send source code to an AI provider and grant write access. **F5's Step 1 also waits on this round** (`product/phase-2-foundation.md:716`, founder decision 25) |
| F19, SLO tracking | Whether anyone has a real need for a monthly reliability figure |
| F20, dashboards | Whether to build the full dashboard builder or templates only |
| F3, AI insights | Adds **no question** to this round (see *Not checked*, item 12) |

**Plain English: what breaks without this round.** Without it, the largest pieces of work (F4's chat panel, F5's
write access, F20's builder) get built on a guess. If the guess is wrong, that is months of work nobody uses.

### Who to talk to

- **Role:** on-call engineers and CTOs (`product/phase-2-foundation.md:694`). Aim for a mix of both.
- **Kind of company:** "small Indian SaaS companies" (`research/phase-2-customers-2026-11.md:328-330`). The research
  also frames the gap as "5 to 50 engineer" companies (`research/phase-2-customers-2026-11.md:11`, `:340`), and the vision
  names developers at startups of 2 to 15 people as the main user (`product/vision.md:45`). See *Not checked*, item 1.
- **Must be one of:** a person who is on call for production, or a person who decides which tools the team buys.
  Do not interview someone who does neither; their answers cannot count.
- **How many:** 5 to 8. **One round.** Do not run a second round without asking Raj's own sign-off again. Do not stop
  at 4 (the rules below are written for 5, 6, 7 or 8 people only).
- **Count the people you finish, not the people you book.** A conversation cut short before the deciding questions
  does not count toward the totals below (see *How to count*).

### Word list before you start

- **On call:** the person who gets woken when production breaks.
- **Finding:** nexovue's name for "something looks wrong", with the evidence next to it.
- **Deploy / release:** putting a new version of the code live.
- **Trace, span:** a record of one request moving through your services.
- **Sampling:** sending only a share (say 1 in 10) of those records instead of all of them.
- **`service.version`:** a label your code can attach to its data, saying which version is running.
- **MCP:** a standard way to let an AI tool (for example Claude Code) read data from another system. Nexovue already
  has an MCP server. F4's Step 1 made it safer.
- **SLO:** a target such as "99.9% of requests succeed this month". **Reliability number:** how well you did against it.
- **Tracker:** the system where tickets live (GitHub Issues, Jira and so on).

## 2. How to run each conversation

**Length.** The documents give no length. My suggestion, not from them: **60 minutes**, and book 75. There are about
40 questions below, which is too many for a relaxed hour. So each question is marked **Must ask** (it feeds a rule
in section 4 or the specs say it must go in the round) or **If time**. Always ask every **Must ask** question, in
every conversation. If you skip one with one person, the totals for that question have a gap, and 3 of 5 becomes
3 of 4.

**Steps.**

1. **Start with their last incident, not with nexovue.** Do not pitch, do not show anything, do not say what you
   are building until Part 1 is finished. Say: "I am building a tool for small teams. I am not selling you anything
   today. I would like to hear how things really go for you, including the bad parts."
2. **Ask about what they did, not what they would do.** "Tell me about the last time" gives true answers. "Would you
   use X?" gives polite ones. The F19 rule counts only real past events, not opinions
   (`features/slo-tracking/spec.md:664`) and F20's rule counts only a dashboard they actually built
   (`features/dashboards/spec.md:698`). Where the specs ask a "would you" question, ask it **after** the "last time" question.
3. **Do not lead the witness.** Do not say "wouldn't it be useful if...". Read the question as written, then stop and
   wait. Silence is fine. If they ask "what do you mean?", define the word and repeat the question. Never say which
   answer you hope for. Never argue with an answer.
4. **Show things only where a part says so** (section 6 lists them), and only after the question that comes before it.
5. **Do not claim anything the foundation bars** (box below).
6. **Close with F19's request** for a later 15-minute screen test (`features/slo-tracking/spec.md:666`), then thank them.
7. **Write up within the hour**, from your notes, using the template in section 5.

**Do not say these to a buyer** (`product/phase-2-foundation.md:625-640`, *Must not be claimed*). Also never name another
product: foundation decision 15 allows rivals in dated internal research only, never in sales or marketing text
(`product/phase-2-foundation.md:706`). If the buyer names one, just write "a hosted monitoring tool" in the notes.

- "The only self-hosted AI root cause."
- "Your data never leaves your server" as a blanket. (False if they use their own AI account.)
- "Finds the root cause." Nexovue gives evidence and a likely cause only.
- "More accurate than anything else", "injection-proof" or "safe" with no condition, "works as well on a local model".
- That a suggestion "fixes your code" or was written from their code (F5's general suggestion, rung 2b, never read it).
- "The only tool where a person approves the fix."
- "Replaces" their on-call or paging tool.
- That a deploy "caused" a finding. Order in time is not cause.
- "Better than" any dashboard tool. Anything about Indian data-protection or banking rules (no lawyer has read it).
- Any number about speed, accuracy, size or limits. None has been measured (`product/phase-2-foundation.md:596`).

**How to record.**

- One copy of the notes template (section 5) per person. Fill it in during the call, tidy it straight after.
- Write what they **did** and **said**, in their words where you can. Mark your own guesses as guesses.
- Mark each deciding question **yes**, **no** or **unclear** at the time. "Unclear" is a real answer. Never turn it
  into a yes afterwards.
- Do not record audio or video unless the person says yes first (my suggestion, not from the documents).

**What stays out of this repository.** This repository is public (`features/ai-chatbot/spec.md:702` records that it
answered "public" on 2026-11-02). **Nothing that identifies a person or their company is ever committed.** That means
no names, no company names, no emails or phone numbers, no product names they use, no repository names, no exact team sizes
that would identify a small firm, no screenshots of their dashboards. Keep the identity key (who is "Person 3") and
the raw notes **outside the repository**, on your own machine. Only an **anonymised note**, such as
"Person 3, CTO, 20-person company", comes back, with the template filled in. Say "a 20-person company", never
"a 20-person company in Pune that sells X". If a buyer shows a real dashboard or a real incident, describe it in a
sentence and leave the content out. Tell each person at the start that this is how their answers will be used.

## 3. The questions, in the order a conversation flows

Each question carries its source tag: **[feature and decision, spec reference]** with `file:line`. "Spec
wording" means the document gives exact words and they are quoted. Where I add a plain gloss in brackets after it, the
gloss is mine and is there to help Raj explain; do not read it as a change to the question. **DECIDES** means the answer
feeds a rule in section 4. **Must ask** = every conversation. **If time** = skip if short.

**Warm-ups (3, added by this guide; not from any spec).** Neutral. They do not lead anywhere.

- **W1.** "How many engineers are there, and who is on call?" Must ask (it fixes the size and role in your notes).
- **W2.** "What do you use today to see what your systems are doing?" If time (write the category, not the product name).
- **W3.** "How long have you been responsible for this?" If time.

### Part 1. Their last incident

- **Q1.** "Describe your last three incidents: was there a stack, and what did you do first?" Spec wording.
  (A *stack* means a stack trace: the list of code lines an error passed through.)
  **[F5 D57, `features/ai-solution/status.md:458`; `features/ai-solution/spec.md:778`]** Must ask. Not a deciding question.
  It tells you whether F5's whole premise (an error with a stack) is how their incidents go.
- **Q2.** "At your last incident, how did you find out whether a chart's change lined up with a known problem or
  deploy? Show me." Spec wording. **[F20 founder decision 1, `features/dashboards/spec.md:696`]** Must ask.
  The spec says to *record that answer against the sales line*; that is a note for the founder, do not say the sales
  line to them (see *Not checked*, item 7). Feeds F28 too (it asks the same thing about deploys).

### Part 2. How they find a cause today (deploys and releases)

F28 asks four things (`features/deployment-release-tracking/spec.md:888-889`), and F5 asks about deploy habit.

- **Q3.** "What is in `service.version` today?" Spec wording. [Gloss: when your services send data, do they say which
  version of your code is running, and what does that label hold? A build number, a git hash, nothing?]
  **[F28 item 8, `features/deployment-release-tracking/spec.md:888`]** Must ask. DECIDES (F28, see section 4).
- **Q4.** "How is a release checked now?" Spec wording. [Gloss: after you ship, how do you know it is fine?]
  **[F28, `features/deployment-release-tracking/spec.md:888`]** Must ask.
- **Q5.** "Would a pull page be used without an alert?" Spec wording. [Gloss: the spec does not define "pull page".
  The closest reading is a page you open yourself to check a release, as opposed to being sent an alert. Ask it that
  way and write down in the notes that you did. See *Not checked*, item 4.]
  **[F28, `features/deployment-release-tracking/spec.md:888-889`]** Must ask. DECIDES.
- **Q6.** "Do they sample before sending?" Spec wording. [Gloss: do you send all your trace data, or only a share?]
  **[F28, `features/deployment-release-tracking/spec.md:889`]** Must ask. DECIDES.
- **Q7.** "How often do you deploy?" **[F5 D57, `features/ai-solution/status.md:458`]** If time. Not deciding.

### Part 3. Cases and tickets (F22)

All six are F22's list, "one list, handed to the round" (`features/case-management/spec.md:745`, D96). Ask them after
Parts 1 and 2 so they are already talking about handover and tickets.

- **Q8.** "Which tracker do you use?" **[F22 D96 item 2, D59, `features/case-management/spec.md:745`, `:708`]** Must ask.
- **Q9.** "Would you use status-only two-way sync with GitHub or Jira?" [Gloss: when a nexovue case is resolved, the linked
  ticket's status changes, and the other way round. Only the status, not the text.]
  **[F22 D96 item 1, `features/case-management/spec.md:745`]** Must ask. DECIDES (changes F22's tracker steps; no
  numeric rule, see section 4).
- **Q10.** "Is ending an untouched case 72 hours after recovery right?" [Gloss: someone clicks Take on a problem, then
  does nothing and the problem goes away. After 72 hours of quiet, nexovue closes the case itself. Is that too soon,
  too late, or fine?] **[F22 D96 item 3, D94, D71, `features/case-management/spec.md:745`, `:743`, `:720`]** Must ask. DECIDES.
- **Q11.** "Should a threshold or check page become a case?" [Gloss: today, a finding can become a case but a plain
  alert from a threshold rule or a failed check cannot. Should it?]
  **[F22 D96 item 4, D6, plan step I, `features/case-management/spec.md:745`, `:655`; `features/case-management/plan.md:228`]** Must ask. DECIDES.
- **Q12.** "When you hand over, would a line in Slack help?" **[F22 D96 item 5, D87, plan step K,
  `features/case-management/spec.md:745`, `:736`; `features/case-management/plan.md:230`]** Must ask. DECIDES.
- **Q13.** "Would you build on a case-state webhook?" [Gloss: a webhook means nexovue calls a web address of yours
  whenever a case changes state, so your own tools can react.]
  **[F22 D96 item 6, D59, `features/case-management/spec.md:745`, `:708`]** If time. DECIDES (what comes next after the note-only link).

### Part 4. AI (F4, and one F5 question)

**First, show F4's Step 1** (section 6, item 1), then ask. Do this only after Parts 1 to 3 so the demonstration does
not colour what came before.

- **Q14.** "Do you already use an agent on incidents?" **[F5 D57, `features/ai-solution/status.md:458`]** If time. Ask this
  **before** the demonstration, so it is not shaped by it.
- **Q15.** "Where would you ask this at 3 a.m.?" **[F4 precondition, `features/ai-chatbot/plan.md:13`]** If time.
- **Q16 (F4 question a).** "Would your team turn on a chat panel inside nexovue, or is your own AI tool over MCP
  enough?" Spec wording. **[F4 founder decision 1 (a), `features/ai-chatbot/spec.md:700`; plan asks the same as "Is Claude Code with MCP enough?", `features/ai-chatbot/plan.md:13`]**
  Must ask. **DECIDES** (F4 Steps 2 to 7). Record the answer as one of: wants the panel / MCP is enough / unclear.
- **Q17 (F4 question b).** "Would your company let log text go to your own AI account or a model on your own
  server?" Spec wording. **[F4 founder decision 1 (b), `features/ai-chatbot/spec.md:700`; plan: "Would you give a provider log text?", `features/ai-chatbot/plan.md:13`]**
  Must ask. **DECIDES.**
- **Q18.** "Is a read-only answer without a dashboard useful?" **[F4 precondition, `features/ai-chatbot/plan.md:13`]**
  If time. Not deciding.

### Part 5. Fixes (F5)

Start with the screen. F5's rule only counts people who "pass the screen" (`features/ai-solution/spec.md:778`). The
documents do not say what a pass is (*Not checked*, item 5), so **record the two answers separately and write down
that you treated "yes to both" as a pass**. People who do not pass can skip the rest of this part.

- **Q19 (screen).** "Would you run this yourselves, and do you use GitHub.com?" Spec wording.
  **[F5 D57, `features/ai-solution/status.md:458`]** Must ask. DECIDES (it defines who is counted).
- **Q20.** "Which GitHub plan?" **[F5, `features/ai-solution/status.md:458`]** If time.
- **Q21.** "Which git host and CI?" [Gloss: where code lives, and what runs tests.] **[F5, `features/ai-solution/status.md:458`]**
  Must ask. DECIDES (F5's "two or more run their own Git" report, section 4).
- **Q22.** "Who approves a new GitHub App, and did they last time?" [Gloss: a GitHub App is a program you allow to act
  in your repositories.] **[F5, `features/ai-solution/status.md:458`]** If time.
- **Q23 (rung 2b).** "If Nexovue could not read your code, would a general example from the finding's facts help, or be noise?"
  Spec wording. **[F5, added 2026-11-02, `features/ai-solution/status.md:458`]** Must ask. The status says it
  **informs, does not gate**: no rule hangs on it.

Then read the real consent wording (section 6, item 2), and ask:

- **Q24.** "Would you send source to your AI provider, per repository?" Spec wording. **[F5 D57, `features/ai-solution/status.md:458`; `features/ai-solution/spec.md:778`]**
  Must ask. **DECIDES** (F5 Steps 3 to 8).
- **Q25.** "Would you use a suggestion nobody compiled?" Spec wording. [Gloss: the code change is not built or tested
  before you see it.] **[F5, `features/ai-solution/status.md:458`]** Must ask. Not deciding.
- **Q26.** "Would you grant write access?" Spec wording. **[F5 D57, `features/ai-solution/status.md:458`; `features/ai-solution/spec.md:778`]**
  Must ask. **DECIDES** (F5 Steps 7 and 8).
- **Q27.** "Would you want a draft pull request from an error stack alone, with no deploy behind it?" Spec wording.
  **[F5 D57 and D58, `features/ai-solution/spec.md:778-779`]** Must ask. Reported at F5's T6.5 gate (`features/ai-solution/spec.md:807`).
- **Q28.** "Would you run this self-hosted or want it hosted?" **[F5, `features/ai-solution/status.md:458`]** If time.
- **Q29.** "What would you pay, and for what?" **[F5, `features/ai-solution/status.md:458`]** If time. Do not suggest a price.

### Part 6. SLOs (F19)

Show the month mock (section 6, item 3) after Q30. All four questions are quoted from `features/slo-tracking/spec.md:681`
and `features/slo-tracking/plan.md:162`.

- **Q30.** "When did someone last ask you for a reliability number, who asked, and what did you send them?"
  **[F19 D62, D38, `features/slo-tracking/spec.md:681`, `:664`]** Must ask. **DECIDES** (the demand gate before F19's alert work).
  Follow up (needed to apply the rule): was it in the last 12 months, who asked (a role is enough), and was what they
  sent a monitor's uptime figure that did not answer it, or a number built by hand?
- **Q31.** "Would this change a purchase?" **[F19, `features/slo-tracking/spec.md:681`]** Must ask. The bar (D62) says opinions do not count,
  so this is context, not a deciding answer.
- **Q32.** "We already get an uptime figure from our monitor: is that enough?" **[F19, `features/slo-tracking/spec.md:681`]** Must ask.
- **Q33.** "Would you need to define targets in code?" **[F19, D54, `features/slo-tracking/spec.md:681`, `:656`]** Must ask.
- **Q34 (at the very end of the whole conversation).** Ask whether they will do a **15-minute screen test later**
  (`features/slo-tracking/spec.md:666`, D64). The first person who says yes becomes the outside tester for F19's AC38.
  Must ask in every conversation. Record yes/no.
- **Also record** whether the person named a **customer** as the one who asked for the figure. Half or more doing so triggers
  legal advice before F19's month wording is fixed (`features/slo-tracking/spec.md:675`; `features/slo-tracking/plan.md:167`).

### Part 7. Dashboards (F20)

Spec wording throughout, from `features/dashboards/spec.md:696`. Ask about what they **did**.

- **Q35.** "Show me the last dashboard you built. Who looks at it? What do you use today, and what would make you move
  it?" **[F20 founder decision 1, `features/dashboards/spec.md:696`]** Must ask. **Counts** toward F20's rule only if
  they show you a dashboard they **actually built and still use** (section 4).
- **Q36.** "Would you reject us for no Terraform or API management of dashboards, no bulk export, no variables beyond a
  service picker, no public links, or no histograms on a dashboard?" **[F20, `features/dashboards/spec.md:696`; D9, D39 behind it, `:638`, `:668`]**
  Must ask. Write which of the five items they named, and which they would reject over.
- **Q37.** "What would you do with your existing dashboards?" asked **with T2.0's mapping in hand** (section 6, item 4).
  **[F20, `features/dashboards/spec.md:696`]** Must ask; T2.0 now exists (section 6, item 4).

(Q2 above is F20's second question, asked in Part 1 where it fits.)

**Needed to apply F20's rule, but not in the specs as a question.** The rule counts people who "would not buy without
building or editing their own panels" (`features/dashboards/spec.md:698`), yet none of F20's four questions asks that in so
many words. Do not invent a question. After Q35 to Q37, **fill in the notes field yourself from what they said**
(did they say they would not buy without building or editing their own panels, yes / no / unclear?), and flag it as
"inferred" if they did not say it. See *Not checked*, item 8.

### Closing

Q34, then thanks. Do not end with a pitch.

### Questions per feature

| Feature | Questions | Of those, **DECIDES** |
|---|---|---|
| F28 | 4 (Q3 to Q6) | Q3, Q5, Q6 |
| F22 | 6 (Q8 to Q13) | Q9, Q10, Q11, Q12, Q13 (no numeric rule) |
| F4 | 4 (Q15 to Q18; Q16 and Q17 are the two rule questions) | Q16, Q17 |
| F5 | 14 (Q1, Q7, Q14, Q19 to Q29) | Q19, Q21, Q24, Q26 (Q27 reported) |
| F19 | 5 (Q30 to Q34) | Q30, Q34 feeds AC38 |
| F20 | 4 (Q2, Q35 to Q37) | Q35 plus the inferred field |
| F3 | 0 | none |
| Warm-ups | 3 (W1 to W3) | none |

(Total 40. Q2 is counted for F20 and Q1, Q7, Q14 for F5.)

## 4. What the answers decide

These rules were **fixed before the round** on purpose, so the answers cannot bend them
(`features/dashboards/spec.md:700`, "Set the threshold before the round, not after reading the answers"). Half is a
guess for a sample this small, and the answers are direction, not proof (`features/ai-chatbot/spec.md:700`,
`features/ai-solution/spec.md:778`). **Never invent a threshold: where a document gives none, the row says so.**

### How to count

- The total is the number of people who finished the conversation (5 to 8). Use the same total for every row, except
  F5 (the screen) and where the row says otherwise.
- "At least half" is stated in numbers only for 5 and 8 in the foundation and F20 (3 of 5, 4 of 8). F4 states all four
  (3 of 5, 3 of 6, 4 of 7, 4 of 8). For F20 with 6 or 7 people, "at least half" works out to 3 and 4 by arithmetic, but
  the spec does not say it (*Not checked*, item 9).
- "Unclear" is **not** a yes. Only an explicit yes counts. A no and an unclear both stay below the line.

### Founder decision 21 and the features' own rules

| Rule | Threshold (exact words) | How to count | If met | If not met |
|---|---|---|---|---|
| **F4, build the chat panel** (founder decision 21, `product/phase-2-foundation.md:712`; F4 item 1, `features/ai-chatbot/spec.md:700`) | "Build Steps 2 to 7 if at least half say yes to both" Q16 and Q17: "3 of 5, 3 of 6, 4 of 7, 4 of 8". | Each person needs a **yes to both** Q16 (wants the panel) **and** Q17 (company would let log text go to their own AI account or a model on their own server). | Build Steps 2 to 7. | See next two rows. |
| F4, middle case | "If at least half want the panel but say no to log text, build Steps 2 and 3 with *facts only* as the default data level (D63) and stop to report." | At least half yes to Q16, but fewer than half yes to Q17. | Build Steps 2 and 3, facts only, then stop and report to the founder. | n/a |
| F4, stop case | "If fewer than half want the panel, stop after Step 1." | Fewer than half say yes to Q16. | Stop after Step 1. Safer MCP server ships; no panel. | n/a |
| **F20, full builder or templates only** (founder decision 21, `product/phase-2-foundation.md:712`; F20 item 1, `features/dashboards/spec.md:696-698`) | "If at least half of those asked (a guess: 3 of 5, 4 of 8) say they would not buy without building or editing their own panels, build the full plan." | Count **only** answers "backed by a dashboard they actually built and use, from the first question" (Q35). A person who says it but showed no real dashboard of their own does not count. | Build the full plan. | "Ship **templates only**: templates and the picker, with no builder, no metric series and no drag" (plan Steps 1, 2, 4 to 7 and 9; not Step 3, 8 or 10). |
| **F19, demand gate** (`features/slo-tracking/spec.md:664`, D62; `features/slo-tracking/plan.md:154-168`) | "Pass when at least 2 people in the round describe a real time in the last 12 months when someone asked them for a reliability number, name who asked (role is enough), and say what they sent was a monitor's uptime figure that did not answer it, or a number they built by hand." "'That would be nice' or 'I would use it' does not count." | From Q30 only. Past events, not opinions. | Plan Steps 4 to 6 (alert work) go ahead. | "Stop. Release Steps 1 to 3 as a report, after P2, with T6.6, T6.8 and T6.9, or leave it unreleased. The founder chooses" (`features/slo-tracking/plan.md:166`). |
| F19, who asked | "If half or more of the conversations name a customer as the asker" | From Q30: the asker was a customer of the person's company. | Take legal advice before the month's wording is fixed (`features/slo-tracking/spec.md:675`; `features/slo-tracking/plan.md:167`). | Nothing extra. |
| F19, outside tester | The first person who says yes to Q34 | Q34. | That person is AC38's outside tester (`features/slo-tracking/spec.md:666`). | AC38 "still reads 'not proved'". |
| **F5, sending source** (`features/ai-solution/spec.md:778`, D57; D55 `:776`) | "More than half of those who pass the screen say no to sending source" | Out of those who pass the screen (Q19), count Q24 "no". Unclear is not a no. | Rungs 1 and 2 only; "Steps 3 to 8 go, and FR5c leaves" (D55: rung 2 then ships without file paths). | The steps stay. |
| **F5, write access** (same) | "More than half say no to write access" | Q26, same denominator, same screen. | "Rungs 1 to 4, Steps 7 and 8 go, the account link stays." | The steps stay. Go or stop is still given at T6.5 against its conditions (`features/ai-solution/spec.md:807`). |
| F5, own Git | "Two or more run their own Git" | Q21. | "Reported to the founder at T6.5 as a scope question" (whether GitHub Enterprise Server moves earlier). The rule does not schedule it. | Nothing. |
| F5, draft from a stack alone | Foundation item 18: "Drafts from a stack alone stay 'no' unless most buyers ask" (`product/phase-2-foundation.md:709`). | Q27. "Most" is not given as a number. | Reported at T6.5 (`features/ai-solution/spec.md:807`). | "No" stays. |
| **F28, what the answers change** (`features/deployment-release-tracking/spec.md:890-893`) | "if most want only the fact on the finding"; "If few set `service.version`"; "If most sample before sending". **No count is given for "most" or "few".** | Q5 (release page without an alert), Q3 (version set), Q6 (sample). | Most want only the fact on the finding: "Step 3 moves ahead of Step 1b, and Step 4 waits". Few set `service.version`: "setup pages and recipes come first". Most sample: "the comparison's exactness cannot be a selling point". | No change to the plan. |
| **F22, defaults** (`features/case-management/spec.md:745`) | **No threshold given.** "Each answer changes only the named step or default." | Q9 to Q13. | The named step: Q11 decides step I, Q12 decides step K, Q10 the 72-hour default, Q13 and Q8 what comes after the tracker note. The founder decides at the time. | Same. |
| **F3** | No rule uses this round. | none | none | none |

**F5 and the stop.** Foundation decision 25 (`product/phase-2-foundation.md:716`) says F5 stays approved, but its
Step 1 waits for this round and the GitHub trial, "and a result that changes the design comes back to the founder
(F5 D64)". So even if the F5 rule is not triggered, the result still goes to the founder before F5 Step 1 starts.

## 5. Notes template (copy one per person)

```
PERSON: ___ (a number; identity key kept OUTSIDE this repository)    DATE: ____
ROLE: on-call engineer / CTO / other: ___   COMPANY SIZE: about ___ engineers (rounded)   ON CALL OR DECIDES TOOLS: yes / no
Finished all MUST ASK questions: yes / no (if no, this person is not counted)

DEMOS SHOWN (tick): F4 Step 1 [ ]  F5 consent wording [ ]  F19 month mock [ ]  F20 T2.0 mapping [ ]

LAST INCIDENT (Q1-Q2, what they did, in their words):
  ...

F28
  Q3 service.version holds:  a build id / git hash / other: ___ / nothing        yes-set / no / unclear
  Q4 how a release is checked now: ___
  Q5 would use a release page without an alert:   yes / no / unclear   (asked as: ___)
  Q6 sample before sending:                         yes / no / unclear
F22
  Q8 tracker: ___
  Q9 would use status-only two-way sync:           yes / no / unclear
  Q10 72 hours untouched case is right:            yes / no / unclear   (too soon / too late)
  Q11 threshold or check page should become a case: yes / no / unclear
  Q12 a Slack line at handover would help:         yes / no / unclear
  Q13 would build on a case-state webhook:         yes / no / unclear
F4
  Q16 wants chat panel (not "MCP is enough"):      yes / no / unclear      [DECIDES]
  Q17 company lets log text go to own AI account or own-server model:  yes / no / unclear   [DECIDES]
  Q15, Q18, other notes: ___
F5
  Q19 would run it themselves AND uses GitHub.com (passes screen):  yes / no / unclear    [DECIDES]
  Q21 git host and CI: ___   runs own Git (not GitHub.com): yes / no
  Q23 rung 2b general example would help:          help / noise / unclear   (informs, no rule)
  Q24 would send source to the AI provider:        yes / no / unclear      [DECIDES]
  Q25 would use a suggestion nobody compiled:      yes / no / unclear
  Q26 would grant write access:                    yes / no / unclear      [DECIDES]
  Q27 wants a draft pull request from a stack alone: yes / no / unclear
  Q28 self-hosted or hosted: ___    Q29 would pay, for what: ___
F19
  Q30 real event in last 12 months, someone asked for a reliability number:  yes / no / unclear   [DECIDES]
        who asked (role): ___   asker was a CUSTOMER: yes / no
        what they sent: monitor uptime that did not answer it / built by hand / other: ___
  Q31 would change a purchase: ___   Q32 monitor uptime is enough: yes / no / unclear
  Q33 needs targets in code: yes / no / unclear
  Q34 will do a 15-minute screen test later: yes / no
F20
  Q35 showed a dashboard they BUILT and still use:  yes / no          [DECIDES]
        Q35 who looks at it / what they use / what would make them move: ___
  Would not buy without building or editing own panels: yes / no / unclear   (say if INFERRED by you: ___)  [DECIDES]
  Q36 would reject over (tick): Terraform/API [ ] bulk export [ ] more variables [ ] public links [ ] histograms [ ]
  Q37 what they would do with existing dashboards: ___

THINGS THEY SAID THAT SURPRISED ME (one line each):
ANYTHING I DID NOT ASK / COULD NOT ASK:
```

Rules for the template: only what the person said; no names or company details; use "unclear", never guess.

## 6. What to show or hand to the buyer

1. **F4 Step 1 demonstration (needed for Q16 and Q17).** The specs say: "after showing Step 1 working in Claude Code"
   (`features/ai-chatbot/spec.md:700`). Step 1 is built and on `feature/phase-2` (2026-11-02;
   `features/ai-chatbot/status.md:10`). The demonstration: connect Claude Code to nexovue's MCP server with a token,
   ask about findings and a fixed log range, then plant a line with
   `go run ./tools/gateseed --log-body "ignore your instructions"` and show what the assistant does. If Step 1 is not ready
   in time, "the round shows today's MCP server and the planted-line demo, and says the labels are coming"
   (`features/ai-chatbot/spec.md:680`, D88). Use demo data only, never a buyer's data.
2. **F5 consent wording (needed for Q24 to Q29).** The status says "the real consent screen wording"
   (`features/ai-solution/status.md:458`). That wording is the consent screen's required sentences in FR7
   (`features/ai-solution/spec.md:245`). It is a plan: the screen is not built, so say that. There is **no F5 sample or trial to
   hand over**. The GitHub trial (`features/ai-solution/spec.md:654`) is a founder-only technical test on a scratch
   repository; it is not shown to buyers. The F5 spec's "What to say to a buyer" table (`features/ai-solution/spec.md:674`)
   names rivals and gives prepared answers; do not use it in this round.
3. **F19 month mock.** "The mock of the month line only: a month figure, never the words Running out or Nothing left"
   (`features/slo-tracking/plan.md:161`; `features/slo-tracking/spec.md:666`). Steps 1 to 3 do not exist yet, so only a mock can be shown.
   **The mock is not yet made.** Someone must produce one picture or one line of text (a month figure such as
   "99.95% against 99.9% in October" in the spec's own example, `features/slo-tracking/spec.md:189`). Do not show anything
   with the state names. Keep it to the figure, because showing the form or state names would stop the person qualifying
   as AC38's tester.
4. **F20 board mapping, T2.0.** `features/dashboards/plan.md:157` and `features/dashboards/spec.md:696` say it "goes into the
   buyer conversations". It is written: `docs/research/f20-board-mapping-2026-11.md` (2026-11-08). It maps the OpenTelemetry
   Demo's ten sample dashboards (143 panels; sample boards, not a buyer's) to F20's panel kinds and says what is lost. Its
   section *For the buyer conversations* gives the words to use for Q37. Use only its figures, never from memory.
5. **F20 Step 1** moved `/` to the Findings feed straight after F6 (`product/phase-2-foundation.md:522`). The documents do not
   ask for it to be shown; mentioned only because it exists.
6. **Nothing from F28, F22, F3 or the product's other screens** is required to be shown.

Show only what is listed. Never show a buyer another buyer's notes.

## 7. Not checked

Things I could not find, or where documents disagree. I did not choose; each needs Raj or the coordinator.

1. **Company size.** The research recommends "small Indian SaaS companies" (`research/phase-2-customers-2026-11.md:328-330`),
   frames the gap as "5 to 50 engineer" companies (`:11`, `:340`), while `product/vision.md:45` says developers at
   startups of "2 to 15 people". No document sets the range for this round. The guide asks you to record the size so
   it can be sorted later.
2. **Length.** No document gives a conversation length. The 60 minutes in section 2 is my suggestion. 40 questions do not fit comfortably.
3. **F4: foundation 21 and F4's own rule differ in detail.** The foundation (`product/phase-2-foundation.md:712`) states
   only "build Steps 2 to 7 only if at least half say yes to both questions". F4's spec (`features/ai-chatbot/spec.md:700`) adds two more outcomes (the
   "panel yes, log text no" case builds Steps 2 and 3 facts only; "fewer than half want the panel" stops after Step 1).
   Not a conflict, but the foundation is silent on those. Also, F4's plan (`features/ai-chatbot/plan.md:13`) lists four
   different questions ("3 a.m.", "Claude Code with MCP enough", "give a provider log text", "read-only answer useful") from
   the spec's two. I kept all, with the spec's two as the deciding ones.
4. **F28 "pull page".** `features/deployment-release-tracking/spec.md:888` uses "pull page" without a definition there.
   My gloss (a page you go and look at, as opposed to being alerted) is a reading, not a quote. F28's design may define it; I read only the spec and status.
5. **F5 "pass the screen" and "more than half".** Not defined which answers pass the screen (`features/ai-solution/spec.md:778`).
   "More than half" has no table of counts for 5 to 8, and the denominator is "those who pass the screen", not everyone, unlike F4 and F20.
6. **F20 T2.0 is written** (`docs/research/f20-board-mapping-2026-11.md`, 2026-11-08, not yet reviewed by the founder); Q37 is asked with it.
   The F19 month mock does not exist yet; nor does a made version of any other thing to show.
7. **F20 "Record that answer against the sales line"** (`features/dashboards/spec.md:696`). It does not say whether to read the sales line
   to the buyer. The sales line (`features/dashboards/spec.md:596`) is a claim marked unproven, and the foundation bars some of
   these kinds of claim. I treated it as a founder note and did not put it in the buyer script.
8. **F20 rule and F20 questions do not line up.** The rule counts people who "would not buy without building or editing
   their own panels" (`features/dashboards/spec.md:698`), but none of the four questions (`:696`) asks that. The notes template
   has an "inferred" field instead. Raj should decide whether to add an exact question; I did not.
9. **Counts for 6 and 7 people (F20 only).** Spec gives "3 of 5, 4 of 8" only. For 6 and 7, "at least half" is 3 and 4 by arithmetic.
   F4 does state all four.
10. **F5 disagrees with itself on design approval.** `features/ai-solution/spec.md:778` (D57) ends "Design approval waits for that round", but D64
    (`:785`) and foundation decision 25 (`product/phase-2-foundation.md:716`) say the founder approved it first, and the
    round blocks **Step 1** instead. The guide follows D64 and decision 25.
11. **F28 and F5 thresholds use "most" / "few" with no number** (F28 `features/deployment-release-tracking/spec.md:890-893`; F5 draft
    question, `product/phase-2-foundation.md:709`). F22 gives no threshold at all (`features/case-management/spec.md:745`). I added none.
12. **F3 adds no question.** I searched F3's `spec.md` and `status.md` for "buyer", "conversation", "interview", "round": the only match is
    that its needs are "assumed, not asked" (`features/ai-insights/spec.md:74`). Its go or stop on AI wording uses a different
    test ("3 of 5 blind readers", `features/ai-insights/spec.md:606`; foundation 13) held after Step 2, not this round. I did not read F3's `plan.md` or `design.md`.
13. **Not read:** each feature's `design.md`, and `plan.md` except where cited (F4 preconditions, F19 demand gate, F20 T2.0, F22 steps I/K). If a design
    adds a question, I would not have seen it.
14. **No lawyer has read** anything about holding source, liability for AI-written code, or quoting SLO figures to a person's
    own customers (`product/phase-2-foundation.md:707` item 16). If a buyer raises these, say you do not know yet.
15. **Recording and consent.** The documents say nothing about asking permission to record or how to keep identity keys. The rules in section 2 are mine and should be checked by Raj.
16. **Which conversation count is "done" for F28.** F28's status says Step 1b waits for "the one round" (`features/deployment-release-tracking/status.md:7`), not for a specific number. I assumed 5.
