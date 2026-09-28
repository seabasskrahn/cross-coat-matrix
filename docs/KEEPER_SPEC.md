# Keeper Spec — Job Envelope

## Overview

Keeper is the Matrix's working memory. It saves every job in progress to a Postgres database so
nothing is lost when the program closes or restarts. The agents run without it today, but they
forget everything the moment the program stops.

## The envelope

Every job carries six fields.

### 1. Message
This is the original thing the user typed to start the job. It's called "message" because it
covers prompts, requests, and briefs.

### 2. Tasks
This is a numbered list, with the dependencies between tasks recorded (for example, task 3 waits
on tasks 1 and 2).

### 3. Agent assignment
Each task carries the agent's name plus a role label, such as `builder` or `researcher`, so it's
always clear who is working on what.

### 4. Draft
This is the output each agent produces. The format adjusts to each agent, and the draft is logged
exactly as produced, whether that's plain text, code blocks, markdown, or something else.

### 5. Approvals
This is a running dialogue thread attached to the job, not a single yes/no flag.

- It supports quick yes/no answers and multi-question requests for more context.
- It allows back-and-forth conversation, like Tony Stark and JARVIS, on an hourly and daily basis.
- Each exchange is tagged as either `quick yes/no` or `context request`.
- The job waits until the thread is resolved.

### 6. Step log
This is a chronological, timestamped list of every action as it happens. Each entry carries one
or more tags from the fixed starter list below, plus freeform tags as needed.

## Log tags

Fixed starter tags:

- `message received`
- `task created`
- `agent assigned`
- `draft saved`
- `approval asked`
- `approval answered`
- `job closed`

Anything unusual gets a freeform tag.

## Where Keeper fits

| Tool | Holds |
| --- | --- |
| GitHub | Code and docs |
| Google Docs | What Seabass reads himself |
| Keeper (Postgres) | The live back-and-forth the agents can't do with either of those |

Keeper runs on Postgres.
