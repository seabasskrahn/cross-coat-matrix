# Cross Coat Matrix — Structure

The town has one centre building, the Central Core. Inside the Core, everything is
arranged in strict levels from top to bottom. The Communications Tower sits at the top
level and is the single point that all traffic routes through. Lines run from the Tower
to every other building in the town.

Service keys marked "planned" are not assigned yet.

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
            K1["Architect — live"]
            K2["Echo — live"]
            K3["Vault — live"]
            K4["Scout — live"]
            K5["Clerk — live"]
            P1["Relay — planned"]
            P2["Keeper — planned"]
            P3["Reserve — planned"]
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
9. Service keys — Architect, Echo, Vault, Scout, Clerk (live); Relay, Keeper, Reserve (planned)

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
