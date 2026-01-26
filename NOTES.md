# 📘 Backend Notes & Mental Models

This file documents **important concepts, design decisions, and “why” explanations** discovered while building the Amino Acid Tracker backend.

Use this as:

- a refresher when returning to the project
- interview prep
- a companion to `README.md` (which stays short)

---

## 1. Docker Basics (Mental Model)

### Dockerfile vs docker-compose.yml

**Dockerfile**

- Describes **how to build ONE container**
- Installs dependencies
- Copies code
- Defines how the app starts (`CMD`)
- `EXPOSE` is documentation only

**docker-compose.yml**

- Describes **how MULTIPLE containers run together**
- Defines:

  - services (api, db)
  - environment variables
  - volumes
  - networks (automatic)
  - host ↔ container ports

Rule of thumb:

> One service → Dockerfile
> Multiple services → docker-compose.yml

---

### `EXPOSE` vs `ports`

**EXPOSE**

- Documents which port the container listens on
- Does NOT open the port
- Not required for container-to-container communication

**ports**

```yaml
ports:
  - "8000:8000"
```

- Makes the service accessible from:

  - your machine
  - the outside world

Important:

> Containers communicate internally via
> `http://service-name:port`
> without exposing ports.

---

## 2. FastAPI & Pydantic Concepts

### `...` (ellipsis) means REQUIRED

Used in:

```python
q: str = Query(...)
grams: float = Field(..., gt=0)
```

Meaning:

- No default value
- User must provide it
- FastAPI automatically returns a 422 error if missing

---

### Why Pydantic Schemas Exist

- ORM models = database representation
- Schemas = API contract

Never return ORM objects directly.

Schemas:

- validate input
- shape output
- prevent leaking DB internals
- keep API stable if DB changes

---

### `from_attributes = True`

```python
class FoodOut(BaseModel):
    ...
    class Config:
        from_attributes = True
```

This allows:

```python
FoodOut.model_validate(food_orm)
```

Why it works:

- SQLAlchemy objects store data as attributes (`food.id`)
- Pydantic normally expects dictionaries
- `from_attributes=True` tells Pydantic:

  > “Read values using dot notation”

Effectively maps:

```text
food.id → id
food.name → name
```

---

## 3. SQLAlchemy Core Concepts

### Session

A **Session** is a “conversation” with the database.

Used to:

- run queries
- insert/update rows
- manage transactions

Each API request gets:

- one session
- automatically closed after request ends

---

### `select()` always returns ROWS

Even this:

```python
select(Food)
```

Returns:

```python
(Food(...),)
```

SQLAlchemy always returns row containers.

---

### `.scalars()`

```python
db.execute(stmt).scalars().all()
```

Purpose:

- Extract the **first column** from each row

Converts:

```python
[(Food(...),), (Food(...),)]
```

Into:

```python
[Food(...), Food(...)]
```

Rule:

- Use `.scalars()` when selecting ONE column or ORM model
- Do NOT use it for joins or multi-column selects

---

## 4. CRUD Layer (Database Access)

**CRUD = Create, Read, Update, Delete**

In this project:

- `crud.py` handles **basic DB access**
- No math
- No business rules

Examples:

- search foods
- fetch amino acids for a food

---

## 5. Business Logic (`logic.py`)

### Why logic is separate from routes

- routes = HTTP + validation
- logic = math + rules

Benefits:

- easier to test
- easier to reason about
- cleaner architecture

---

### Amino Acid Unit Conversion (Critical)

Database stores:

> mg per 100g

User inputs:

> grams eaten

Conversion:

```text
factor = grams / 100
actual_mg = mg_per_100g × factor
```

Example:

- 2600 mg / 100g
- 150g eaten
- → 3900 mg

---

### Recommendation Scoring (MVP)

Initial scoring rule:

```text
score(food) = sum(mg/100g of limiting amino acids)
```

Why this is good:

- simple
- explainable
- extensible later (diet filters, calories, preferences)

---

## 6. ETL Pipeline (CSV → Database)

### ETL = Extract → Transform → Load

- Extract: read CSV rows
- Transform:

  - strip whitespace
  - lowercase amino acid names
  - convert strings → floats

- Load:

  - insert/update database tables

---

### `get_or_create` Pattern

Used for:

- Source
- Food

Logic:

- check if row exists
- reuse if found
- otherwise insert new

Makes ETL:

- idempotent
- safe to re-run
- resistant to duplicates

---

## 7. `flush()` vs `commit()` (Very Important)

### `db.add()`

- Stages object in session
- No SQL executed
- `id` is still `None`

---

### `db.flush()`

> Sends SQL to DB without committing

Effects:

- INSERT/UPDATE runs
- DB generates primary key
- object.id becomes available
- changes are NOT permanent yet

Used when:

- you need generated IDs before commit (foreign keys)

---

### `db.commit()`

> Makes changes permanent

- Data is saved
- Other sessions can see it
- Cannot rollback afterward

---

### Why ETL uses `flush()` + one `commit()`

- `flush()` → get IDs
- `commit()` once → performance + atomicity

If anything fails:

- entire transaction rolls back
- database remains clean

---

## 8. End-to-End API Flow

1. Frontend sends request
2. FastAPI:

   - validates input (schemas)
   - injects DB session

3. Route calls `crud` or `logic`
4. Logic queries DB + computes results
5. Pydantic serializes output
6. JSON returned to frontend

---

## 9. Design Principles Used

- Separation of concerns
- Schema-driven APIs
- Idempotent ETL
- Provenance tracking (sources + links)
- Containerized, reproducible setup
- Resume-grade backend architecture

---

## 10. Interview One-Liners

- “`from_attributes=True` allows safe serialization of ORM objects into API schemas.”
- “`flush()` gets generated IDs, `commit()` persists data.”
- “Docker Compose orchestrates multiple services locally.”
- “Business logic is isolated from CRUD and routes for testability.”

---

## 11. Difficulties

- I ran into an issue when running the ETL script where Python raised ModuleNotFoundError: No module named 'app'. This happened because running a script directly (e.g. python etl/ingest_csv.py) sets the import root to the script’s directory instead of the project root, so the app package could not be resolved. I fixed this by moving the ETL script inside the application package and running it as a module using python -m app.etl.ingest_csv, which ensures Python resolves imports correctly without relying on sys.path hacks.

## 12. Improvement in design

- An improvement I made was starting with a simple local workflow by downloading/building a small CSV myself (great for validating the schema, ETL logic, and API responses quickly), then upgrading the design to ingest data directly into Postgres for automation and reliability. Keeping CSV as an optional export/debug artifact avoids “CSV as a database” problems, makes scheduled ingestion easier, and is a cleaner, more production-style pipeline.

- Tried calling apis as the user searched, ended up being too inefficient and caused throttling. Download the bulk data on a weekly basis and stored the failed searches, ran the failed searches through an nlp model to filter real models and used another nlp model to check papers for this food to search for data there.
