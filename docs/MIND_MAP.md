# Cross Coat Matrix — Structure

The town has one centre building, the Central Core. Inside the Core, everything is
arranged in strict levels from top to bottom. The Communications Tower sits at the top
level and is the single point that all traffic routes through. Lines run from the Tower
to every other building in the town.

Each service desk shows its nickname, the key or service behind it, its job, and its status.

```mermaid
flowchart TD
    subgraph CORE["CENTRAL CORE"]
        direction TB
        CT["Communications Tower"]
        CEO["Seabass — CEO"]
        STW["STEWARD"]
        BZL["BEZEL"]
        UNIT["The Unit"]
        SUN["SUNDAY"]
        LUM["LUMEN"]
        MER["Merovingian — building manager role"]
        CB["Circuit breakers"]

        subgraph KEYS["Service keys"]
            direction LR
            K1["Architect (XAI_API_KEY)<br/>Grok brain for STEWARD<br/>Status: active"]
            K2["Echo (GOOGLE_API_KEY)<br/>Gemini brain for BEZEL<br/>Status: active"]
            K3["Vault (GITHUB_TOKEN)<br/>Reads and pushes code to GitHub<br/>Status: key works, unwired"]
            K4["Scout (TAVILY_API_KEY)<br/>Live web search<br/>Status: key works, unwired"]
            K5["Clerk (WORKSPACE_DIR)<br/>Reads and writes project files<br/>Status: works, unwired"]
            P1["Relay (n8n)<br/>Routes messages and Telegram approvals<br/>Status: planned, not running"]
            P2["Keeper (Postgres)<br/>Working memory, saves every job<br/>Status: live"]
            P3["Reserve (Claude)<br/>Backup brain, no set purpose yet<br/>Status: off"]
        end

        CT --> CEO
        CEO --> STW
        CEO --> BZL
        STW --> UNIT
        BZL --> UNIT
        UNIT --> SUN
        SUN --> LUM
        LUM --> MER
        MER --> CB
        CB --> KEYS
    end

    CT --> FIELD
    CT --> MONEY
    CT --> QUAL
    CT --> FILM
    CT --> MAIL
    CT --> MKT
    CT --> CAL
    CT --> RES
    CT --> TRN

    subgraph FIELD["Field Operations"]
        TAP["TAPER"]
        ARM["ARMOR"]
    end

    subgraph MONEY["Finance and Revenue Defense"]
        MAR["MARGIN"]
        AUD["AUDIT"]
        DED["DEDUCT"]
        LED["LEDGER"]
    end

    subgraph QUAL["Quality and Blueprint"]
        FIN["FINISH"]
        VEC["VECTOR"]
    end

    FILM["Film and Content — planned"]
    MAIL["Email — planned"]
    MKT["Marketing and Advertising — planned"]
    CAL["Calendar — planned"]
    RES["Deep Research — planned"]
    TRN["Training Grounds — planned"]
```

## Levels inside the Central Core

1. Communications Tower
2. Seabass — CEO
3. STEWARD and BEZEL (equal rank)
4. The Unit
5. SUNDAY
6. LUMEN
7. Merovingian — building manager role
8. Circuit breakers
9. Service keys (desks):
   - Architect (`XAI_API_KEY`): Grok brain for STEWARD. Active.
   - Echo (`GOOGLE_API_KEY`, Gemini): Gemini brain for BEZEL. Active.
   - Vault (`GITHUB_TOKEN`): reads and pushes code to GitHub. Key works, no agent uses it yet.
   - Scout (`TAVILY_API_KEY`): live web search. Key works, no agent uses it yet.
   - Clerk (`WORKSPACE_DIR`): file tool that reads and writes project files. Works, no agent uses it yet.
   - Relay (n8n): routes messages and Telegram approvals. Planned, not running.
   - Keeper (Postgres): working memory that saves every job. Live.
   - Reserve (Claude): backup brain. Off, no clear purpose yet.

## Buildings connected to the Communications Tower

- Field Operations — TAPER, ARMOR
- Finance and Revenue Defense — MARGIN, AUDIT, DEDUCT, LEDGER
- Quality and Blueprint — FINISH, VECTOR
- Film and Content — planned
- Email — planned
- Marketing and Advertising — planned
- Calendar — planned
- Deep Research — planned
- Training Grounds — planned
