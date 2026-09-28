# Cross Coat Matrix — Mind Map (end-goal vision)

This is the planning picture of the Matrix, not a description of what is coded today.
Service keys are shown by their nicknames. Live keys are in `.env` and working;
planned keys are not assigned yet.

```mermaid
flowchart TD
    CEO["SEABASS — CEO<br/>Neo, the One<br/>Final say on everything"]

    CEO --> STW["S.T.E.W.A.R.D.<br/>The Architect<br/>Lives in the core reactor<br/>Brain key: Architect"]
    CEO --> BZL["B.E.Z.E.L.<br/>Business Execution & Zero-effort Extension Liaison<br/>The Oracle, sits in the office with you<br/>Brain key: Echo"]

    STW --> UNIT
    BZL --> UNIT
    UNIT["THE UNIT<br/>You + STEWARD + BEZEL<br/>One voice to the world: Stuart"]

    UNIT --> SUN["S.U.N.D.A.Y.<br/>Sovereign Unaudited Network for Dispatch & Asset Yield<br/>The Operator, routes every call in and out"]

    SUN --> MER["THE MEROVINGIAN<br/>Building manager role<br/>One in charge of each building, with its own rules and people"]

    STW -.-> LUM["LUMEN — The Trainman<br/>Builds and maintains the Matrix<br/>Option B: no body, the green code itself"]
    LUM -.-> MER

    MER --> FIELD
    MER --> MONEY
    MER --> QUAL
    MER --> FUTURE
    SUN --> CB

    subgraph FIELD["Field Operations building"]
        TAP["T.A.P.E.R.<br/>Tailgate Agenda & Project Execution Relay<br/>Morpheus, crew captain"]
        ARM["A.R.M.O.R.<br/>Advanced Rig & Mobile Operations Relay<br/>Niobe, keeps the Silverado running"]
    end

    subgraph MONEY["Finance & Revenue Defense building"]
        MAR["M.A.R.G.I.N.<br/>Market Analysis & Revenue Growth for Incremental Net<br/>Pricing"]
        AUD["A.U.D.I.T.<br/>Autonomous Undisclosed Dividend & Invoice Tracker<br/>The Sentinels, hunts down what's owed"]
        DED["D.E.D.U.C.T.<br/>Digital Expense Depreciation & Unclaimed Capital Tracker<br/>Tax write-offs"]
        LED["L.E.D.G.E.R.<br/>Loss-prevention, Expense Dispatch & General Equity Reconciliation<br/>QuickBooks, always needs your yes"]
    end

    subgraph QUAL["Quality & Blueprint building"]
        FIN["F.I.N.I.S.H.<br/>Final Inspection, Network Intelligence & System Hand-off<br/>Seraph, tests work before it goes out"]
        VEC["V.E.C.T.O.R.<br/>Value Estimation, Calculation & Takeoff Operational Relay<br/>The Keymaker, exact cuts and counts"]
    end

    subgraph FUTURE["Planned buildings (switched off, slots ready)"]
        FILM["Film / content"]
        MAIL["Email"]
        MKT["Marketing & advertising"]
        CAL["Calendar"]
        RES["Deep research"]
        TRN["Training grounds<br/>The Construct / the Dojo"]
    end

    CB["Circuit-breaker supervisors<br/>The Agents, stop runaway loops"]

    subgraph KEYS["Service keys"]
        subgraph LIVE["Live"]
            K1["Architect<br/>(Grok)"]
            K2["Echo<br/>(Gemini)"]
            K3["Vault<br/>(GitHub)"]
            K4["Scout<br/>(Tavily web search)"]
        end
        subgraph PLANNED["Planned, not yet assigned"]
            P1["Clerk<br/>(file tool)"]
            P2["Relay<br/>(n8n)"]
            P3["Keeper<br/>(Postgres)"]
            P4["Reserve<br/>(Claude)"]
        end
    end

    K1 -.-> STW
    K2 -.-> BZL
```
