# Design principles

> **Type:** Design standard · **Status:** Written 2026-09-12.
> **Companion docs:** [design-system.md](design-system.md) (the visual system these produce) ·
> [`../engineering/frontend.md`](../engineering/frontend.md) (the enforced coding rules)

Ten rules that decide a screen nobody has designed yet. [design-system.md](design-system.md) answers
*what colour, what component, what token*; this file answers *what should be on the screen at all*.

Each rule states what breaks without it. A principle that cannot be violated is decoration.

---

## 1. The primary object is a sentence

"Checkout is 4× slower than normal since 15:04." Everything else on the screen is evidence
supporting a sentence. Charts are evidence. Tables are evidence. The sentence is the product.

**Without it:** fifty dashboards — the thing this product exists to replace. A screen that opens on a
grid of panels has already lost, no matter how good the panels are.

**Check:** can a reader say what is wrong, in one sentence, without interpreting a chart? If the
answer requires them to compare two lines themselves, the screen is unfinished.

## 2. A real zero and "we don't know" never look alike

A missing reading renders as an em dash. Never `0`, never `—` for a true zero, never a blank cell.

**Without it:** a customer reads "0 errors" from a service that stopped reporting entirely, and
concludes the opposite of the truth. This is the single most dangerous failure an observability
product can have, because the interface is confident and wrong.

**Check:** enforced in the formatters under `apps/web/src/shared/format/`. A new formatter that
returns `0` for an absent reading is a defect, not a style choice.

### The one named exception: the errors cell

`formatErrors` in `shared/format/health.ts` renders the em dash for **zero errors**, not only for an
absent count. That is deliberate: a wall of `0` down a fifty-row list is noise a reader has to scan
past, and health being the absence of colour reasonably extends to the absence of a number.

**It is safe only because the ambiguous case cannot occur.** `error_count` is `number` in the API
contract and `int(row["error_count"])` from a SQL `SUM` on the server — a service that appears in a
list always has a real count. The em dash in that column therefore always means zero, and a reader
is never left unable to tell "nothing broke" from "we cannot see it".

**The condition is the important half.** If `error_count` ever becomes optional on any endpoint that
feeds this cell, the em dash becomes ambiguous and this exception stops being safe. Changing that
field's optionality means changing this formatter in the same commit.

## 3. Every screen owes its states, and some it must not write

A screen is not designed until every state it owns is. There are two kinds, and confusing them is
how the same rule gets implemented eleven different ways.

### Owned by the screen

| State | What it must do |
|---|---|
| **Loading** | A skeleton that holds the layout still and says what is coming — never a spinner |
| **Empty** | Say why it is empty and what to do next |
| **Zero results** | A filter that matched nothing is *not* the empty state. Name the filter, offer to clear it. A customer with eleven services must never be told they have none |
| **Partial** | Some readings missing — rule 2 applies per cell, and the screen still renders. Never hide a row because its evidence is incomplete |
| **Validation** | Inline, beside the field, and in a summary when a form has several |
| **Unsaved changes** | Editing and navigating away warns before the work is lost |
| **Dangerous** | An irreversible action states its blast radius in real numbers before it is possible to confirm |
| **Success** | Say what happened and where it went, not just that it worked |

### Owned centrally — a screen must not reimplement these

`app/query-client.ts` is the single place HTTP status is interpreted, and `AppShell` owns
connectivity. A feature that writes `if (error.status === 401)` has duplicated a rule rather than
followed it.

| State | Where it lives |
|---|---|
| **401 session expired** | Clears the session, goes to login, and returns the reader to where they were |
| **403 forbidden** | The route boundary renders it — a property of the screen, not a passing event |
| **404 not found** | Rendered inside the shell, because someone who mistypes a URL is already inside the app |
| **429 rate limited** | A toast honouring `Retry-After` |
| **5xx server error** | A toast with a retry, carrying the correlation id |
| **Offline** | One shell banner, and no toast on top of it |

**Without it:** the states get invented per screen under time pressure and disagree with each other.
The empty state is where a new customer spends their first ten minutes and is the state least likely
to be designed; the zero-result state is the one most often collapsed into it.

**Check:** `docs/engineering/frontend.md` requires loading, empty, error and mobile. This extends it
to the full set, and names the six a screen must *not* write itself.

## 4. Reuse before invention

Read `apps/web/src/shared/components/` before building a control. If something close exists, extend
it. If the same thing is now built in three feature folders, it belongs in `shared/`.

**Without it:** three buttons that are almost the same, and the generated look arrives one defensible
component at a time — the exact failure `verify-tokens.js` was written to catch in the palette, but
in the component layer where no script is watching.

**Check:** this one is human. `VirtualTable` sat past its own stated threshold of five callers until
it reached nine, unnoticed, while this document was deleted.

## 5. Native elements first

A `<select>` stays a `<select>`. A custom control must justify rebuilding keyboard navigation,
typeahead, screen-reader semantics and the mobile picker — all of which the platform does better.

**Without it:** an accessibility regression nobody tests for, and a mobile experience that is worse
in a way desktop review never surfaces.

## 6. Colour is never the only carrier

Every severity also has a shape, a weight, and a position. Health is the absence of colour, not
green. Brand green is interactive-only — never a status, never a chart series.

**Without it:** the interface is unreadable for roughly one man in twelve, and a wall of green dots
shouts that nothing is happening — which contradicts the product.

**Check:** `verify-contrast.js` measures contrast and colourblind lightness separation. It cannot
check whether colour is the *only* carrier; that is a review question.

## 7. An icon never carries meaning alone

Icons earn their place where a word would be worse — dismiss, disclosure, external link. Navigation
is an index of words.

**Without it:** Lucide-as-the-entire-visual-language, which is on the banned list in
[design-system.md](design-system.md) and is one of the most recognisable AI-generated signatures.

**Check:** if removing the icon makes the interface unusable, the label is missing.

## 8. Every user-visible error carries its correlation id

The id is what turns "it broke" into a log line and a trace. Status handling happens once, in
`app/query-client.ts` — never per screen.

**Without it:** a support conversation that cannot be resolved, in a product whose entire premise is
that failures are explainable.

## 9. Density is a feature, and the reader is reading

This is closer to a terminal than a marketing page. The user is scanning for an anomaly, not
glancing at a summary. Whitespace that would flatter a landing page costs a reader rows they needed.

**Without it:** the uniform rounded-card grid — correct for a marketing site, wrong for someone
trying to find the one slow endpoint among four hundred.

**Balance:** density is not crowding. Rule 1 still governs — the sentence gets room; the evidence
gets packed.

## 10. The design is decided before React, not inside it

For a screen that does not yet exist, the user's job, the flow, the six states and the reused
components are settled and approved **before** the first component is written. React implements an
approved design; it is not where the design gets discovered.

**Without it:** the UI becomes evidence that an agent can generate React, rather than evidence that
anyone understood the customer's problem. This is the rule the other nine exist to make checkable.

**Scope, deliberately narrow:** this applies to a new screen or a materially changed one. A trace
table is a solved problem and copying the convention is correct. The rule earns its cost on screens
with no convention to copy — the Findings feed above all, where the design *is* the product thesis.

---

## Applying these

A change that adds or changes a screen states, before implementation: the user's job, the flow, the
six states, which existing components it reuses, and **what it deliberately does not show**. That
last one is the hardest and the most valuable — an insight-first product is defined by what it
refuses to put on the screen.
