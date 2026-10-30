# Bug: a name from telemetry becomes markup in a Slack, Teams or Discord alert

## Reported behavior

Found on 2026-10-30, while the F6 design's security review read the alert payload builders. Alerts are
built from names the product does not control:

- service names (threshold and *went silent* rules);
- the error group's message (*new error*), which is raw exception text;
- check and rule names.

A public browser site key accepts spans and errors from anyone, so anyone can choose those names. Slack,
Teams and Discord read the text as their own markup:

- a name such as `<!channel> <https://evil.example|Reset your password>` posts a channel-wide ping and a
  disguised link in the customer's own Slack;
- `[Reset](https://evil.example)` becomes a disguised link in Teams and Discord;
- a newline in a name starts a line the message never wrote, such as a fake `Rule:` line, in every
  channel, email included.

## Reproduction

Four regression tests were written before the fix (see *Regression test*) and run against
`origin/main` at `bf335d5`:

```
uv run pytest -q apps/scheduler/tests/test_alert_payloads.py apps/scheduler/tests/test_alert_messages.py
E       AssertionError: assert '<!channel>' not in '[critical] ...ple) is down'
E       assert False                       # Teams: '[x](' and '[Reset](' present
E       AssertionError: assert '[Reset](' not in '<!here> *bo...ynthetics/c1'
E       AssertionError: assert ('\n' not in '[critical] ...xxxx is down'
4 failed, 23 passed in 0.38s
```

## Root cause

`apps/scheduler/src/nexovue_scheduler/alerts/payloads.py` writes `AlertMessage.summary` and
`details` into Slack's `text`, Teams' Adaptive Card `TextBlock.text` and Discord's embed `title` and
`description` unchanged. All four are markup fields. `alerts/messages.py` (`compose`) puts
`subject_label` and `rule_name` into those strings as given, control characters and length included.

Discord's `allowed_mentions: {"parse": []}` already stops pings there. It does not stop a disguised
link.

## Customer impact

No customer is affected today, because nexovue is pre-deployment.

For a real customer, anyone who can reach their public site could put a channel-wide ping or a phishing
link inside the customer's own alert channel. The message would come from the customer's trusted
monitoring bot, about the customer's own service. That is a credible lure for "reset your nexovue
password". After the first customer, every alert ever sent through these channels would have been
exposed in the same way.

## Specification impact

None. F21's FR17 (each channel's native format) and BR9 (a message states nothing from the evidence)
both still hold. A message's words are now shown as words.

## Regression test

- `apps/scheduler/tests/test_alert_payloads.py`:
  - `test_slack_shows_telemetry_text_literally_and_keeps_its_own_link`
  - `test_teams_shows_telemetry_text_without_a_link_in_it`
  - `test_discord_shows_telemetry_text_without_markdown`
- `apps/scheduler/tests/test_alert_messages.py`:
  - `test_a_name_from_telemetry_cannot_add_lines_or_run_on`
  - `test_a_batch_names_its_rule_on_one_line_too`, added at review. It was seen failing without the fix:
    `assert 'Down\nRule: forged' == 'Down Rule: forged'`.

The first four were seen failing before the fix (*Reproduction*), and pass after it. Two existing tests,
which compared the Discord and Teams summary with the raw summary, now compare it with the escaped
form. `[critical]` is sent as `\[critical\]`, and still displays as `[critical]`.

CI's first run found a third existing test of the same kind, which the offline run here had not reached.
`apps/scheduler/tests/test_alert_senders_live.py` checked for the raw summary in the body sent to Slack,
Teams and Discord. For Teams and Discord it failed, as it should once the markup is escaped. It now looks
for each channel's escaped form among the body's decoded strings. Checked offline against the three
real payloads: the old check fails for Teams and Discord, and the new one passes all three. The live
suite itself still needs the stack, and runs in CI.

## Fix plan and validation

- **`messages.py`:** a new `one_line()` turns C0 and C1 control characters, and U+2028 and U+2029,
  into a space. It caps a name at 200 characters (`NAME_LIMIT`). `compose` applies it to the subject's
  label and the rule's name, so every channel, email included, gets one line of plain words.
  `compose_batch` takes the rule names from the composed messages.
- **`payloads.py`:**
  - **Slack:** `slack_text()` escapes `&`, `<` and `>`, with `&` first. Slack's documentation says
    these are the only characters it decodes, under *Escaping text* in
    [Formatting text for app surfaces](https://api.slack.com/docs/formatting).
  - **Teams:** `teams_text()` escapes the backslash and the brackets.
    - **Unverified** against Microsoft's documentation, which has no escaping rule for
      `TextBlock`. Its source is [Review Board's tested change](https://reviews.reviewboard.org/r/13892),
      which found that only bracket escapes render correctly on Teams mobile.
  - **Discord:** `discord_text()` backslash-escapes its markdown characters.
    - **Unverified** against Discord's documentation, which does not cover escaping in embeds. That
      embeds render disguised links is reported in
      [vercel/chat#567](https://github.com/vercel/chat/pull/567).
  - Each channel's own link, the "Open in nexovue" link, is added after escaping, so it stays a link.
- **Validation:** `just check` (*Status*). The rendering in a real Slack, Teams and Discord workspace has
  not been checked: none is connected to this environment.

## Status

See [`status.md`](status.md).
