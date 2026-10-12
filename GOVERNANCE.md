# Governance

How connectors enter the Jaah catalog, how they are reviewed and kept healthy, and how they leave it.
Adapted from the [Hummingbot](https://hummingbot.org/) connector process — community-built connectors,
proposals before code, a maintainer per connector, a published removal policy, and three integration
tiers with a bounty board — with two differences: decisions are made by Jaah maintainers in GitHub issues
(there is no token voting), and all discussion happens on GitHub (issues and Discussions), not Discord.

How to *build* a connector is in [CONTRIBUTING.md](CONTRIBUTING.md).

## 1. Roles

| Role | Who | Does |
|---|---|---|
| **Contributor** | Anyone with access to this repo | Proposes, builds and submits connectors and fixes. |
| **Connector maintainer** | The person named in the connector's `maintainer` field | Keeps one connector working (§5). |
| **Jaah maintainer** | Members of the `jaah-ai` GitHub org with *Maintain* or *Admin* on this repo | Decides JGPs, reviews, merges, publishes to the catalog, removes. |
| **Sponsor** | An org (or Jaah) funding a bounty or a sponsorship (§7) | Funds and pays; has no merge rights. |

## 2. Proposals (JGP)

Every new connector starts as a **JGP** — a Jaah Governance Proposal — before any code is reviewed.

1. **Open** an issue with the *Connector proposal* template, titled `JGP-<N>: Add <X> connector`, `<N>`
   being the issue's own number. It says what the connector does, its type, personas, what it needs
   (binaries, credentials, network reach), who will maintain it, and the integration tier (§7). It gets
   the `JGP` label.
2. **Discussion period: 7 calendar days** from opening, on the issue. Anyone may comment. A Jaah maintainer
   replies within that period with any concerns (overlap with an existing connector, security reach, an
   unmaintainable dependency).
3. **Decision.** After the period, a Jaah maintainer closes the discussion with a comment starting
   **`Accepted`** or **`Declined`** and the reason. Accepted proposals stay open until the connector merges
   (the PR closes them); declined ones are closed. A declined JGP may be reopened with a changed proposal
   after 30 days.
4. A proposal unanswered by its author for 30 days during discussion is closed as stale.

Changes to *this* document are proposed the same way (`JGP-<N>: <change>`), with a 14-day discussion
period, and decided by Jaah maintainers.

## 3. Review

A submitted connector PR is reviewed by a Jaah maintainer who is not its author. The reviewer walks the
checklist below and **pastes it into the PR as a comment, every box ticked or explained**. Each item says
how to check it; none needs the contributor to be asked. The behaviour each item verifies is defined in
[CONTRIBUTING.md](CONTRIBUTING.md) (§ numbers in brackets).

### Technical

- [ ] **T1 Manifest.** `python3 tools/check_connectors.py connectors/<name> --sdk-version <current SDK>`
      prints `ok` (CI runs it). The directory name equals `name`; `version` is bumped from `develop`. [§2–3]
- [ ] **T2 Structure.** README and `tests/` exist; nothing outside `connectors/<name>/` is imported; no SDK
      in any install file (T1 checks); dependencies, if any, are pinned with `==` and `[runtime] install`
      matches whether there are any. [§2]
- [ ] **T3 One terminal call per path.** Every exit path of the entrypoint ends in exactly one `settle` or
      `fail`, made last. Check: read each return/raise path of the entrypoint; each is a test in `tests/`.
      [§5.1]
- [ ] **T4 No daemonizing.** `grep -rnE "setsid|nohup|os\.fork|systemd-run|start_new_session" --exclude-dir=tests
      connectors/<name>` finds nothing that detaches from the entrypoint's process tree. [§2]
- [ ] **T5 Type and personas fit.** The ending the code makes matches §4's table for its `type` and every
      persona it claims. [§4]
- [ ] **T6 Usage.** Every model call is followed by `report_usage` (or the connector makes none — say so).
      [§5.6]
- [ ] **T7 README.** Has the header table (Type, Personas, Language, Needs on the node, Needs on the
      credential roster, Talks to, Maintainer) and the step-by-step behaviour, Full Auto and security-gate
      sections. [§6]

### Security

- [ ] **S1 Credentials.** Secrets come only from `credential(name)`: every hit of
      `grep -rnE "environ|getenv|open\(" --exclude-dir=tests connectors/<name>` reads something that is not a
      secret. [§5.5]
- [ ] **S2 No secret written.** A test asserts the credential value never reaches a daemon call, the events
      file, an artifact or a commit; secrets reach child processes only through `env=`, never argv. [§5.5]
- [ ] **S3 Security gate.** For `coding`/`cli_wrapper`: tests show (a) attended + security-surface diff →
      one `Security` round, merge only on approve, auto-merge never armed; (b) Full Auto → draft PR and
      `fail` whose reason starts with the surface and carries the PR URL. [§5.4]
- [ ] **S4 Full Auto asks no decision.** A test with `autonomous: true` shows no `ask` except with a
      `Credential` / `Access` / `Human act` / `External` header. [§5.3]
- [ ] **S5 Untrusted code cannot reach the socket.** If the connector runs model-chosen commands, the
      child's env drops `JAAH_AGENT_SOCKET` and `JAAH_AGENT_EVENTS`, asserted by a test. [§5.7]
- [ ] **S6 Network reach.** Every host the code contacts is in the README's "Talks to"; nothing else. [§5.8]
- [ ] **S7 Writes.** Files are written only in the worktree, `$JAAH_REPORT_DIR` and temp dirs the connector
      creates. [§2]

### QA

- [ ] **Q1 CI green**: manifest check, the connector's tests, and the **80% line-coverage floor**. [§7]
- [ ] **Q2 Test paths**: happy path, nothing-to-do, a question round, a failure, Full Auto — each a named
      test. [§7]
- [ ] **Q3 Live run on Jaah dev**: the reviewer registers the PR's head commit as an org agent on Jaah dev
      (sub-path `connectors/<name>`) and runs one task end to end; the PR comment records the task URL,
      that the session shows the agent's logs and usage, and the outcome (merged PR / report / named
      failure). A connector that needs a credential the reviewer cannot obtain records **"Q3 deferred:
      <what is missing>"** and stays unpublished until Q3 is done.

A PR merges only with every item ticked (Q3 may be deferred as above). **Security findings block**: an S
item that fails is fixed in the PR or the PR is declined.

## 4. Merging and publishing

- **Who merges:** a Jaah maintainer — never the PR's author, never a bot — once the checklist comment is
  complete and CI is green. Merges go to `develop`; history is kept (merge commits, no squash).
- **Publishing:** a Jaah superadmin publishes the merged commit to the catalog by its **exact sha** (the
  catalog never follows a branch). An update to a published connector is a new PR, a new review, and a new
  publish; until then orgs keep running the reviewed commit.

## 5. Connector maintainers

Each connector names one maintainer (`maintainer` in its manifest). By accepting the role they commit to:

1. **Respond within 14 days** to issues filed against the connector, and fix breakage reported as a bug.
2. **Keep it working on the current SDK**: when a new SDK minor version ships, update the `sdk` range (and
   code, if needed) within **30 days**.
3. **Keep its dependencies patched**: a published security advisory against a dependency is fixed within
   14 days.
4. **Hand over openly**: a maintainer stepping down says so in an issue; another contributor (or Jaah) may
   take the role by a PR changing `maintainer`, merged by a Jaah maintainer.

## 6. Removal

A connector is removed from the catalog for one of these reasons:

| Reason | Means |
|---|---|
| **Broken** | Its CI or its Q3 live run fails on the current SDK, and no fix has merged. |
| **Unmaintained** | Its maintainer missed a §5 obligation and did not respond to a reminder within 14 days. |
| **Insecure** | It has an unfixed vulnerability, or it leaks or misuses credentials. |
| **Low use** | Fewer than 3 orgs have it enabled for two consecutive quarters. |

**Procedure.**

1. A Jaah maintainer opens a removal issue, `Remove <connector>: <reason>`, with the evidence, mentions the
   maintainer, and posts the notice in GitHub Discussions (*Announcements*).
2. **Notice period: 30 days**, during which a fix (for broken / unmaintained / insecure) or a new maintainer
   cancels the removal.
3. After the period a Jaah superadmin marks the catalog entry **removed** and a PR deletes the directory.
   Orgs that enabled it see it marked *Removed from catalog*, and tasks using it fail to start with that
   reason rather than silently falling back to another agent.
4. **Insecure** connectors skip the notice: they are marked removed immediately, and the issue and notice
   follow.

## 7. Integration tiers and bounties

Like Hummingbot's exchange connectors, a connector reaches the catalog one of three ways:

| Tier | Who builds | Who pays | Who maintains |
|---|---|---|---|
| **DIY** | Any contributor, after an accepted JGP | Nobody | The contributor |
| **Bounty** | A contributor who claims the bounty | The sponsor, to the contributor, on merge | The contributor, unless the bounty says otherwise |
| **Sponsorship** | Jaah (or a contractor Jaah names), under an agreement with the sponsoring org | The sponsor, to Jaah | Jaah, for the agreement's term |

### Bounty process

Bounties are GitHub issues labelled `bounty` plus exactly **one** status label. The status labels are the
board:

| Label | Means | Who sets it |
|---|---|---|
| `Open` | Funded and waiting for a contributor | A Jaah maintainer, after checking the issue |
| `Assigned` | One contributor is working on it | A Jaah maintainer |
| `Submitted` | A PR implementing it is open | A Jaah maintainer (or the contributor) |
| `Merged` | The PR merged after review (§3–4) | A Jaah maintainer |
| `Paid` | The sponsor paid the contributor | A Jaah maintainer, on both parties' confirmation |

1. **Post.** The sponsor opens an issue with the *Bounty* template: what to build (usually an accepted
   JGP), the amount and currency, the deadline, the payment method, and acceptance beyond §3. A Jaah
   maintainer checks it and labels it `bounty` + `Open`.
2. **Claim.** A contributor comments with a short plan and an estimate. A Jaah maintainer assigns the
   first suitable claim and moves it to `Assigned`. One assignee at a time.
3. **Inactivity.** An assignee with no commit or comment for 14 days is unassigned and the bounty goes back
   to `Open`.
4. **Submit.** The contributor opens the PR (`Implements #<bounty issue>`) → `Submitted`. Review is §3,
   exactly as for any connector.
5. **Merge.** On merge → `Merged`.
6. **Pay.** Payment is made **directly by the sponsor to the contributor, off-platform** (bank transfer,
   invoice — as the bounty states), within 30 days of merge. Jaah does not hold, route or guarantee money.
   When both confirm on the issue, a Jaah maintainer labels it `Paid` and closes it.

A bounty abandoned by its sponsor before `Assigned` is closed; after `Assigned`, the sponsor must give 14
days' notice on the issue before withdrawing it.

## 8. Conduct

Discussion stays on GitHub (issues, PRs, Discussions) and on the work. Jaah maintainers may lock threads,
close issues, and remove contributors who harass others or act in bad faith.
