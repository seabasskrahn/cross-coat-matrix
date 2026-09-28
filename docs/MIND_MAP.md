# Cross Coat Matrix — Mind Map (end-goal vision)

This is the planning picture of the Matrix, not a description of what is coded today.
The Central Core is the skyscraper in the middle of everything. Service keys are shown
by their nicknames. Live keys are in `.env` and working; planned keys are not assigned yet.

## 3D layout: the city from above

The Central Core skyscraper stands in the exact centre of the Matrix. Every other
building stands in a ring around it, at equal distance, so the Core can reach each one
directly. Going clockwise from the front door: Field Operations, Finance and Revenue
Defense, Quality and Blueprint, then the planned buildings for Film and Content, Email,
Marketing and Advertising, Calendar, Deep Research, and the Training Grounds. Planned
buildings stand in the ring switched off until they are opened.

```mermaid
mindmap
  root((CENTRAL CORE skyscraper))
    Field Operations
    Finance and Revenue Defense
    Quality and Blueprint
    Film and Content - planned
    Email - planned
    Marketing and Advertising - planned
    Calendar - planned
    Deep Research - planned
    Training Grounds - planned
```

## Chain of command: who reports to whom

```mermaid
flowchart TD
    CEO["SEABASS — CEO<br/>Neo, the One<br/>Final say on everything"]

    subgraph CORE["CENTRAL CORE — the skyscraper in the middle of everything"]
        STW["S.T.E.W.A.R.D.<br/>The Architect<br/>Lives in the core reactor<br/>Brain key: Architect"]
        BZL["B.E.Z.E.L.<br/>Business Execution & Zero-effort Extension Liaison<br/>The Oracle, sits in the office with you<br/>Brain key: Echo"]
        UNIT["THE UNIT<br/>You + STEWARD + BEZEL<br/>One voice to the world: Stuart"]
        SUN["S.U.N.D.A.Y.<br/>Sovereign Unaudited Network for Dispatch & Asset Yield<br/>The Operator, routes every call in and out"]
        MER["THE MEROVINGIAN<br/>Building manager role<br/>One in charge of each building, with its own rules and people"]
        LUM["LUMEN — The Trainman<br/>Builds and maintains the Matrix<br/>Option B: no body, the green code itself"]
        CB["Circuit-breaker supervisors<br/>The Agents, stop runaway loops"]

        subgraph KEYS["Service keys"]
            subgraph LIVE["Live"]
                K1["Architect<br/>(Grok)"]
                K2["Echo<br/>(Gemini)"]
                K3["Vault<br/>(GitHub)"]
                K4["Scout<br/>(Tavily web search)"]
                K5["Clerk<br/>(file tool)"]
            end
            subgraph PLANNED["Planned, not yet assigned"]
                P2["Relay<br/>(n8n)"]
                P3["Keeper<br/>(Postgres)"]
                P4["Reserve<br/>(Claude)"]
            end
        end

        STW --> UNIT
        BZL --> UNIT
        UNIT --> SUN
        SUN --> MER
        SUN --> CB
        STW -.-> LUM
        LUM -.-> MER
        K1 -.-> STW
        K2 -.-> BZL
    end

    CEO --> STW
    CEO --> BZL

    MER --> FIELD
    MER --> MONEY
    MER --> QUAL
    MER --> FUTURE

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
```
