# Personal Assistant with Knowledge-Graph Memory (Graphiti + Neo4j)

Hey, I'm **Miguel Otero Pedrido** — founder of [The Neural Maze](https://theneuralmaze.substack.com/).
This is the code companion I put together for **Issue #04 — Graphs for Agentic
Systems** of *The AI Systems Engineer Journey*.

It's a personal assistant that **remembers you and the people in your life**.
Every exchange is folded into a temporal knowledge graph (via
[Graphiti](https://github.com/getzep/graphiti)) stored in
[Neo4j](https://neo4j.com/). You can watch the graph assemble itself node by
node as you chat.

I deliberately kept any agentic framework out of this — just `graphiti-core`,
Neo4j, and a thin chat loop — so you can see exactly what's happening.

---

## What you need

- **Docker + Docker Compose** (for Neo4j)
- **Python 3.10+**
- An **OpenAI API key** (Graphiti defaults to OpenAI for entity extraction + embeddings)

## Setup

```bash
# 1. Start Neo4j (Browser on :7474, Bolt on :7687)
make up            # or: docker compose up -d

# 2. Configure your keys
cp .env.example .env
#    then edit .env and paste your OPENAI_API_KEY

# 3. Install Python deps (a virtualenv is recommended)
pip install -r requirements.txt
```

### Managing Neo4j with the Makefile

| Command | What it does |
|---|---|
| `make up` | Start Neo4j in the background |
| `make down` | Stop Neo4j but **keep** the data volume |
| `make delete` | Stop Neo4j **and wipe** the `neo4j_data` volume (fresh start) |
| `make wipe` | Full teardown: containers + volume + orphans + locally-built images |
| `make logs` | Tail Neo4j logs |
| `make ps` | Show container status |

Use `make delete` whenever you want to start over from an empty graph.

## Run it

You have two entry points. Both connect to the same Neo4j instance and share
the graph-memory layer in `app/memory.py`.

### Option A — the scripted demo (`python -m app.seed_demo`)

Best for a quick, reproducible walkthrough. It replays a fixed list of episodes
about me — my work on The Neural Maze, my stack, my projects — and ends with me
moving from **Almería** to **Vigo**, so you can watch the temporal model
supersede the old `LIVES_IN` fact.

```bash
python -m app.seed_demo
```

**Step by step, here's what happens:**

1. **Connect & build indices.** `memory.setup()` calls Graphiti's
   `build_indices_and_constraints()` against Neo4j. On a *fresh* database this
   creates ~30 indices/constraints. On a database that already has them, Neo4j
   reports each as already-existing.
2. **Ingest each episode in order.** For every line in `EPISODES`,
   `memory.remember()` calls `add_episode()`, which:
   - sends the text to OpenAI to **extract entities** (people, places, things)
     and the **relationships** between them;
   - **searches the existing graph** to see whether those entities/edges are
     already present (so it merges instead of duplicating);
   - writes new nodes/edges and **time-stamps** each relationship with a
     `valid_at`.
   You'll see `[1/8] ingesting: ...` progress lines. Each one is an LLM call, so
   it takes ~0.5–2s.
3. **The fact change.** The final episode says I no longer live in Almería and
   now live in Vigo. Instead of deleting the old `LIVES_IN → Almería` edge,
   Graphiti stamps it with an `invalid_at` and adds a new
   `LIVES_IN → Vigo` edge. **Nothing is lost — history is preserved.**
4. **Done.** The script prints the Cypher query to run and closes the
   connection.

> Re-running the seed adds to the existing graph. For a clean slate, run
> `make delete && make up` first.

### Option B — the interactive assistant (`python -m app.assistant`)

Best for exploring the behaviour yourself, in your own words.

```bash
python -m app.assistant
```

**Step by step, each time you type a message, the loop does three things:**

1. **RECALL** — `memory.recall(your_message)` runs Graphiti's hybrid search
   (semantic + keyword + graph) over *your* namespace and returns the most
   relevant facts. This step does **not** call an LLM, so it's fast.
2. **GENERATE** — those facts are injected into the system prompt and the LLM
   writes a reply that's grounded in what it already knows about you.
3. **REMEMBER** — `memory.remember()` folds the exchange back into the graph
   (the same extract → reconcile → time-stamp pipeline as the seed demo). You'll
   see a brief `...updating memory graph...` status while the LLM extraction
   runs.

Try it: tell it your name, where you live, and a few people you know. Then say
something *changed* ("I just moved to a new city", "I switched jobs") and watch
how the old fact gets superseded rather than erased. Type `quit` to exit — your
graph stays in Neo4j for you to explore.

## Watch the graph form

1. Open **http://localhost:7474**
2. Log in with user `neo4j`, password `demodemo123`
3. Run this Cypher query (re-run it after each exchange):

```cypher
MATCH (n)-[r]->(m) RETURN n, r, m
```

You'll see nodes for the people and places in your life appear, edges connect
them, and — when you contradict an earlier fact — the temporal handling kick in:
the old relationship gets an invalidation timestamp rather than disappearing.

### Verify the graph structure (memory)

These queries confirm Graphiti actually built the memory: episodes, the entities
it extracted from them, and how everything connects. (The seed demo writes to
the `demo_user` namespace — swap `'demo_user'` for your own `group_id` if you
changed it.)

```cypher
// 1. Quick census — how many of each kind of node and relationship exist?
MATCH (n)            RETURN labels(n) AS label, count(*) AS count ORDER BY count DESC;
MATCH ()-[r]->()     RETURN type(r)   AS rel,   count(*) AS count ORDER BY count DESC;
```

```cypher
// 2. The episodes — the raw exchanges I fed in, newest first.
MATCH (e:Episodic {group_id: 'demo_user'})
RETURN e.name AS episode, e.content AS text, e.created_at AS created
ORDER BY e.created_at DESC;
```

```cypher
// 3. The entities Graphiti extracted (people, places, things) and their summaries.
MATCH (n:Entity {group_id: 'demo_user'})
RETURN n.name AS entity, labels(n) AS labels, n.summary AS summary
ORDER BY n.name;
```

```cypher
// 4. Which episode mentioned which entity? (provenance: how a fact entered memory)
MATCH (e:Episodic {group_id: 'demo_user'})-[:MENTIONS]->(n:Entity)
RETURN e.name AS episode, collect(n.name) AS entities_mentioned
ORDER BY episode;
```

```cypher
// 5. The full entity graph — just the people/places/things and the facts linking them.
MATCH (n:Entity)-[r:RELATES_TO]->(m:Entity)
RETURN n, r, m;
```

### Verify the temporal component

This is the heart of the demo: when a fact changes, Graphiti **invalidates** the
old edge (stamps `invalid_at`) and adds a new one — it never deletes history.

```cypher
// 6. Every relationship fact with its validity window. valid_at = when it
//    became true; invalid_at = when it was superseded (null = still current).
MATCH ()-[r:RELATES_TO]->()
RETURN r.fact AS fact, r.valid_at AS valid_from, r.invalid_at AS valid_until
ORDER BY r.valid_at;
```

```cypher
// 7. Currently-true facts only (no invalidation timestamp yet).
MATCH ()-[r:RELATES_TO]->()
WHERE r.invalid_at IS NULL
RETURN r.fact AS current_fact, r.valid_at AS since
ORDER BY r.valid_at;
```

```cypher
// 8. Superseded (historical) facts — the ones a later episode contradicted.
MATCH ()-[r:RELATES_TO]->()
WHERE r.invalid_at IS NOT NULL
RETURN r.fact AS old_fact, r.valid_at AS was_true_from, r.invalid_at AS invalidated_at
ORDER BY r.invalid_at;
```

```cypher
// 9. The Almería -> Vigo move, side by side. After the seed demo you should see
//    the Almería edge with an invalid_at, and the Vigo edge still open (null).
MATCH (me:Entity)-[r:RELATES_TO]->(place:Entity)
WHERE place.name IN ['Almería', 'Vigo', 'Almeria']
RETURN place.name AS city, r.fact AS fact,
       r.valid_at AS valid_from, r.invalid_at AS valid_until;
```

## Understanding the console output

The first time you run either script you may see a wall of scary-looking log
lines. **In almost all cases the run still succeeds** — the scripts print
`Done.` / `ready` at the end. Here's what each kind means.

The two entry points already call `quiet_graphiti_logs()` (in `app/memory.py`)
to suppress the first two categories below. If you removed that call, or run
Graphiti yourself, you'll see them again. They are informational, not failures.

| You see... | What it means | Should you worry? |
|---|---|---|
| `Neo.ClientError.Schema.EquivalentSchemaRuleAlreadyExists` (many lines) | `build_indices_and_constraints()` tried to `CREATE INDEX ... IF NOT EXISTS`, but the index already exists from a previous run. Idempotent. | **No.** Cosmetic. Run `make delete` if you want a truly fresh DB. |
| `01N52 property key does not exist` for `name_embedding`, `fact_embedding`, `episodes`, `entity_edges` | Graphiti's internal dedup searches reference properties that don't exist yet on an empty graph. They stop appearing once data is written. | **No.** First-run-only noise. |
| `Source entity not found in nodes for edge relation: ...` | The extraction LLM proposed a relationship but didn't also emit one of its endpoint nodes (an extraction inconsistency, common when one sentence packs in several entities). Graphiti **drops that single edge** and continues. | **Mostly no.** That one relationship may be missing. Re-run, or split dense sentences into shorter single-fact lines (as the seed demo now does). |

So: the index "errors" and `01N52` warnings are safe to ignore entirely. Only
the `Source entity not found` line indicates a (minor, recoverable) skipped edge.

## How it works

```
you type a message
      │
      ▼
recall(query)  ──►  Graphiti hybrid search (semantic + keyword + graph)  ──►  facts
      │                                                                         │
      ▼                                                                         ▼
generate reply  ◄──────────────  facts injected into the LLM prompt  ◄─────────┘
      │
      ▼
remember(exchange)  ──►  Graphiti extracts entities + relationships,
                          time-stamps edges, merges into Neo4j
```

Each user gets their own `group_id` namespace, so one assistant can serve many
people without their memories mixing.

## Files

| File | What it does |
|---|---|
| `docker-compose.yml` | Neo4j database |
| `Makefile` | Start/stop/wipe the Neo4j container (`make up` / `down` / `delete`) |
| `app/memory.py` | The graph-memory layer (`remember` / `recall`) over Graphiti |
| `app/llm.py` | Minimal OpenAI client for replies |
| `app/assistant.py` | Interactive chat loop |
| `app/seed_demo.py` | Scripted conversation incl. a fact change |
