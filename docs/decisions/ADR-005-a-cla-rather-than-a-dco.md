# ADR-005: Contributions come with a CLA, not a DCO

> **Status:** Accepted, 2026-09-15 — founder decision · **Depends on:**
> [ADR-004](ADR-004-agpl-with-a-permissive-browser-sdk.md), which makes the licence AGPL-3.0 and the
> repository public
> **Blocks:** F37, which is the change that actually makes the repository public

## The question

The repository is about to be public under AGPL-3.0. The moment an outside patch is merged, its
author owns the copyright in those lines. They have licensed them inbound under the project's
licence, but they have not given them away.

So a choice has to be made before the first pull request, not after: does a contributor sign
something that preserves the ability to relicense, or only certify that the code was theirs to give?

## Decision

**A Contributor License Agreement.** Contributors keep their copyright and grant a licence broad
enough to include releasing their contribution under other terms.

## Why, and why it cannot wait

**Sole copyright is the thing being protected.** [ADR-004](ADR-004-agpl-with-a-permissive-browser-sdk.md)
turns on it twice: relicensing stays possible because one person holds every line, and AGPL was
chosen specifically to stop someone running a modified nexovue as a closed service. The commercial
answer to that threat is to sell exceptions yourself — a private licence, without the publishing
obligation, to a customer whose lawyers will not accept AGPL. **That is only sellable while we can
license the whole codebase.** One merged patch from someone who has signed nothing ends it.

**The two directions are not equally reversible, and that is the whole argument.**

| | Cost |
|---|---|
| CLA now → DCO later | Stop asking. Free. |
| DCO now → CLA later | Every past contributor must sign. One refusal, one dead email address, one person who has lost interest — and their code must be located and rewritten from scratch, provably. Sometimes impossible. |

**There are zero contributors today**, so the option costs nothing to hold and cannot be bought back
later. This is the cheapest it will ever be.

## What was rejected

**A DCO** — a `Signed-off-by` line certifying the contributor had the right to submit the code. Near
zero friction, and it does give the provenance guarantee that protects us from someone pasting in
their employer's code. It does not transfer or licence copyright, so relicensing ends at the first
patch. Right for a community-governed project; wrong for one person building a business, which is
why the Linux kernel uses it and Grafana, Elastic, MongoDB and Sentry do not.

**Nothing at all.** The default, and the reason this ADR exists: doing nothing *is* a decision, made
silently, on the day the first pull request arrives.

## The cost, stated honestly

A CLA deters some contributors. Signing is a real step, and a minority of developers refuse on
principle. If nexovue only ever earns from hosting — which is today's plan — a DCO would have been
enough, and this ADR is an option premium paid for a business model that may not happen. It is
priced at a few contributors we will never know we lost.

## What still has to be done, and what must not be improvised

**The agreement text is a legal document and will not be drafted here.** Two established templates
fit; both are freely usable and widely deployed, and one of them should be adapted rather than
anything written from scratch:

- **Apache ICLA** — the most recognised. Contributors know it on sight, which lowers the friction
  this decision costs. Its grant is written around distribution by the recipient, so the relicensing
  scope wanted here has to be confirmed rather than assumed.
- **Harmony Agreements** (harmonyagreements.org) — purpose-built for exactly this case, with an
  explicit choice of outbound licensing terms, in both a licence and an assignment variant. The
  licence variant, with the broad outbound option, is what this decision describes.

**Have it reviewed before the repository is public.** Publishing with a CLA that does not grant what
this ADR assumes is worse than publishing with a DCO, because it reads as protection that is not
there.

The mechanism is settled and is not the hard part: a CLA bot (SAP's cla-assistant, or the
`contributor-assistant/github-action` variant that stores signatures in-repository) comments on each
pull request and blocks the merge until the author has signed. It lands with F37, alongside
`CONTRIBUTING.md`, because F37 is the change that makes any of this reachable.

## Consequence for later

**On incorporation, the signed CLAs go to the company along with the copyright.** ADR-004 already
records that copyright does not follow incorporation by itself. Neither do these — a CLA naming
ANANDHARAJ A as the recipient grants rights to him, not to a company that did not exist when it was
signed. Whoever drafts the assignment should cover both.
