# Cross Coat Matrix — Timeline: from map to reality

How the [mind map](MIND_MAP.md) gets built, one phase at a time. Each phase says what gets
built, roughly when, and what "done" looks like. Nothing moves to the next phase until the
current one is done and you've said yes.

**Rules that hold in every phase:** you are the only CEO. The AI drafts; it never sends, pays,
deletes or decides without your yes. Keys live only in `.env`.

> **About the numbers.** Dates, costs and percentages are my estimates from what we've built
> and discussed, not measured data. Costs marked *verified* were checked on the provider's site
> on Sep 27, 2026. Model costs depend on how much the Matrix is used and will be replaced with
> real figures once we have a few weeks of usage.

## The whole road at a glance

```
PHASE 0        PHASE 1          PHASE 2             PHASE 3              PHASE 4          PHASE 5          PHASE 6
Groundwork --> Memory & Build --> Communications --> Merovingian &   --> Live Mind   --> 3D Town     --> New Buildings
(done)         Crew               Tower              Live Buildings      Map             (Sims-style)     & Product
Sep 26-27      Weeks 1-2          Weeks 3-4          Weeks 5-8           Weeks 9-10      Months 3-4       Month 4+
```

## Scorecard

| Metric | P0 Groundwork | P1 Memory & Build Crew | P2 Comms Tower | P3 Merovingian & Buildings | P4 Live Mind Map | P5 3D Town | P6 New Buildings & Product |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Rough timing | Done | Weeks 1-2 | Weeks 3-4 | Weeks 5-8 | Weeks 9-10 | Months 3-4 | Month 4+ |
| Daily running cost (est.) | Under $1 | $0.50-$2 | $1-$3 | $2-$5 | $2-$5 | $2-$6 | Grows per building |
| Completion odds | 100% | 90% | 80% | 70% | 80% | 55% | 60% |
| Vibe-coding friction | Low | Low | Medium | Medium | Low | High | Medium |
| Code retained into the final product | 90% | 90% | 85% | 85% | 70% | 95% | 90% |
| Your hands-on time | Done | 1-2 hrs/week | 2-3 hrs once, then little | 2-4 hrs/week | Under 1 hr | 2-3 hrs/week testing | Varies |
| Business value when done | Low | Medium | High | Very high | Medium | Medium | High |

*New metrics added to Gemini's set:* **your hands-on time**, because your time on site is the
scarcest resource, and **business value when done**, so you can see which phases pay off for
Cross Coat soonest. *Dropped:* "3D engine hookup" as its own metric; it's covered by Phase 5.

Gemini-style gauges for the phases that carry the most risk:

```
P3 Merovingian & Buildings
  Vibe-coding synergy   [#######...]  70%
  Follow-through        [#######...]  70%
  Token predictability  [########..]  80%   (circuit breakers cap runaway loops)
  Game-engine alignment [#########.]  90%   (LangGraph states map to avatar actions)

P5 3D Town
  Vibe-coding synergy   [#####.....]  50%
  Follow-through        [#####.....]  55%
  Token predictability  [#########.]  90%
  Game-engine alignment [##########] 100%
```

---

## Phase 0 — Groundwork (done, Sep 26-27)

**What was built**
- The project on your PC and the private GitHub repo `seabasskrahn/cross-coat-matrix`.
- The Matrix engine on LangGraph: SUNDAY routes each message to STEWARD or BEZEL, who delegate
  to specialists; anything that changes the outside world stops at the approval gate for your yes.
- Brain switching between Grok and Gemini with one line in `.env`.
- Keys tested and live: Architect (Grok), Echo (Gemini), Vault (GitHub), Scout (Tavily), Clerk (file tool).
- The rename panel, the mind maps, and the Keeper comparison. 23 automated tests passing.

**Done looks like:** ✅ reached.

## Phase 1 — Memory & Build Crew (weeks 1-2)

**What gets built**
1. **Keeper goes live.** A Neon database (recommended; free plan, *verified*) replaces the
   engine's temporary in-memory storage. The Postgres connector is already in the project's
   pinned packages, so this is a small, contained change.
2. **LUMEN joins the code** as the builder, reporting to whichever brain is active.
3. **Each senior staff member gets its own brain:** STEWARD on Grok, BEZEL on Gemini.
4. **Scout, Vault and Clerk get wired to LUMEN** so it can research, read and write files in
   its locked workspace, and save code to GitHub, all behind the approval gate.
5. **Circuit breaker, version 1:** a hard cap on steps and tokens per task. If a task hits it,
   the task stops and you're told why.
6. Housekeeping: fix the S.T.E.W.A.R.D. spelling-out once you give it to me.

**Done looks like**
- Close the chat, reopen it, and the Matrix remembers the earlier conversation.
- Ask LUMEN for a small change; it drafts the code, you say yes, and it lands on a GitHub branch.
- A deliberately looping test task gets stopped by the circuit breaker, with a message saying why.

## Phase 2 — Communications Tower (weeks 3-4)

**What gets built**
1. **Relay (n8n) becomes the Communications Tower**, the single point all traffic passes through.
   Self-hosting is free; the cloud version is paid (price to be checked at the start of this phase).
2. **Telegram approvals:** drafts arrive on your phone with Yes / No buttons.
3. SUNDAY takes messages in from Telegram and sends replies back out through the Tower.

**Done looks like:** from the jobsite, you text the Matrix, get a draft back, and approve it with
one tap. Every message shows up in the Tower's run log.

## Phase 3 — Merovingian & live buildings (weeks 5-8)

**What gets built**
1. **A Merovingian manager for each building**, sitting under SUNDAY and over that building's workers.
2. **The approval ladder:** worker, then supervisor, then the building's Merovingian, then the
   active brain, then you. Each level settles what it can and passes the rest up.
3. **Circuit breaker, version 2:** each level gets a small fixed allowance to settle a disagreement
   before it escalates to the next level.
4. **Real apps behind each building,** one building at a time:
   - *Finance first:* QuickBooks Online read-only for AUDIT and MARGIN; LEDGER may only draft
     entries, which always need your yes.
   - *Field Operations:* crew day plans for TAPER, truck and rig records for ARMOR.
   - *Quality & Blueprint:* takeoff maths for VECTOR, final checks for FINISH.

**Done looks like:** one real Cross Coat job flows end to end: VECTOR drafts the takeoff, MARGIN
prices it, FINISH checks it, and LEDGER drafts the invoice, which reaches you for a yes.

## Phase 4 — Live mind map (weeks 9-10)

**What gets built:** a web page showing the Matrix as a live 3D node graph (a free graph library
such as 3d-force-graph), fed by the rename panel's names and the Tower's run data.

**Done looks like:** you watch a message travel from the Tower through SUNDAY to a building, with
each node lighting up as it works.

## Phase 5 — 3D town, Sims-style (months 3-4)

**What gets built**
- The town from your picture: the **Central Core** skyscraper in the centre with the
  Communications Tower on top, STEWARD's **core reactor**, BEZEL in the office with your avatar,
  and the buildings in a ring around it.
- An avatar at a desk for each active agent. Talking to an avatar gives exactly the same result
  as typing in chat, because both go through the same engine.
- Built in a browser 3D engine (Three.js) or a game engine (Godot); decided at the start of the phase.
- Open decision carried forward: LUMEN as a person, or as the falling green code on the Core.

**Done looks like:** you walk your avatar to TAPER's desk, ask for tomorrow's crew plan, and get the
same draft you'd get in chat.

## Phase 6 — New buildings & product (month 4 onward)

**What gets built:** the planned buildings switch on one at a time from their ready-made slots:
email, calendar, deep research, marketing and advertising, film and content, and the training
grounds. After that, packaging the Matrix so other trades owners can use it.

**Done looks like:** each new building passes the same test as Phase 3 (one real task end to end
with your approval), and the Matrix runs Cross Coat's daily paperwork with you approving from your phone.

---

## What's decided and what's still open

| Decided | Still open |
| --- | --- |
| You are the only CEO; STEWARD and BEZEL are equal senior staff | Keeper: Neon (recommended) or your own PC |
| The Central Core with the Communications Tower on top | LUMEN as a person or as the green code |
| One Merovingian manager per building | The S.T.E.W.A.R.D. spelling-out |
| Service key nicknames: Architect, Echo, Vault, Scout, Clerk (live) | Whether Reserve (Claude) gets a job |
| LangGraph as the engine; n8n as the hub | Three.js or Godot for the 3D town |
