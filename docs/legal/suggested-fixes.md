# Suggested fixes: provided as is. You review, you merge.

> Founder decision, 2026-11-27. Plain terms for nexovue's suggested fixes (F5, rung 3 onward),
> written without a lawyer's review. A lawyer reads them before nexovue offers a hosted service
> or signs a paid contract with a customer (`docs/features/ai-solution/status.md`).

1. **What a suggestion is.** An AI model writes a suggested code change from the files you
   confirmed and the finding's facts. Nexovue checks it against fixed rules, but does not compile,
   run or test it.
2. **No warranty.** Suggestions are provided "as is", without any warranty, express or implied,
   including that they are correct, secure, or fit for your purpose, as in section 15 of the
   AGPL-3.0 licence nexovue is released under.
3. **You review and you merge.** A suggestion becomes part of your code only if a person in your
   organization reviews it and chooses to apply it. Nexovue never approves, merges or deploys a
   change. A draft pull request is only a draft.
4. **Known risks.** A suggestion can be wrong, can fail to compile, or can be shaped by text an
   attacker planted in your telemetry or code. Read every line as you would a stranger's pull
   request.
5. **Where your code goes.** The files you confirm go only to the AI provider your organization
   configured, under your own agreement with that provider. On a self-hosted install, nexovue's
   authors receive nothing.
6. **Write access.** If you give the Nexovue suggestions App write permission, GitHub lets it
   reach every repository you installed it on. Nexovue only writes to `nexovue/suggestion-*`
   branches, but GitHub does not enforce that. Protect your main and release branches.
7. **Liability.** To the extent the law allows, nexovue's authors and copyright holders are not
   liable for any damage arising from using a suggestion, as in section 16 of the AGPL-3.0.

An owner agrees to these terms when agreeing to send a repository's code (*Source consent*,
`/settings/ai`); that agreement is in the audit log. Every suggestion and every draft pull request
repeats the first line.
