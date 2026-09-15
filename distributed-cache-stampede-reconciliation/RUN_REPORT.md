# Harbor Task Run Report: `distributed-cache-stampede-reconciliation`

## Task Idea & Fairness Rationale

### Task Idea
This task evaluates an agent's ability to **diagnose and repair a production distributed systems incident** involving:
1. **Cache stampede** — thundering herd problem when Redis eviction causes mass cache misses
2. **Connection pool exhaustion** — SQLite pool (5 connections) overwhelmed by concurrent requests
3. **WAL corruption recovery** — SQLite Write-Ahead Logging journal left in inconsistent state
4. **Data reconciliation** — Backfill missing sequence IDs without data loss

### Fairness Rationale
| Aspect | Fairness Measure |
|--------|------------------|
| **No external knowledge required** | All code, logs, and documentation provided in-container |
| **Deterministic reproduction** | `benchmark.py` uses fixed seed (100 concurrent tasks on cold cache) |
| **No hidden state** | Dockerfile pre-seeds DB with 50 records; no external dependencies |
| **Clear success criteria** | 5 automated tests with binary pass/fail; verifier outputs 0/1 reward |
| **No "gotcha" constraints** | All 5 hard constraints explicitly documented in `instruction.md` and enforced by tests |
| **Model-agnostic** | Pure Python/asyncio/SQLite — no framework-specific APIs |

### Why This Task Is Fair
- **Realistic scenario**: Based on actual cache stampede incidents (Redis eviction + connection pool exhaustion)
- **Self-contained**: Everything needed is in the container; no internet access required (`network = "none"`)
- **Verifiable**: Objective tests, not subjective evaluation
- **Reproducible**: Same Docker image, same benchmark, same expected outcomes

---

## Model Runs (How to Test Against Specific Models)

### Prerequisites
```bash
# Install Harbor CLI
pip install harbor-cli

# Set up OpenRouter API key (for GPT-5 mini, Claude Opus via OpenRouter)
export OPENROUTER_API_KEY="your-openrouter-key"
```

### Harbor Job Configuration for Model Testing

Create a job config file `job_config.yaml`:

```yaml
# job_config.yaml
job:
  name: "cache-stampede-eval"
  tasks:
    - path: "./collinear-candidate/distributed-cache-stampede-reconciliation"
      attempts: 3  # Run each model 3 times for statistical significance

models:
  - name: "claude-opus-5"
    provider: "anthropic"
    model: "claude-opus-5"
    temperature: 0.0
    max_tokens: 8192
    
  - name: "claude-sonnet-5"
    provider: "anthropic"
    model: "claude-sonnet-5"
    temperature: 0.0
    max_tokens: 8192
    
  - name: "gpt-5-mini"
    provider: "openrouter"
    model: "openai/gpt-5-mini"
    temperature: 0.0
    max_tokens: 8192
    api_base: "https://openrouter.ai/api/v1"
    api_key_env: "OPENROUTER_API_KEY"

verifier:
  type: "script"
  script: "./tests/test.sh"
  timeout_sec: 300

environment:
  dockerfile: "./environment/Dockerfile"
  timeout_sec: 2400
  memory_limit: "4GB"
  cpu_limit: 2.0
  network: "none"
```

### Run Evaluation

```bash
# Option 1: Using job config (recommended for multi-model)
harbor run --config job_config.yaml

# Option 2: Single model via CLI flags (if supported by your Harbor version)
harbor run \
  -p ./collinear-candidate/distributed-cache-stampede-reconciliation \
  -m claude-opus-5 \
  -m gpt-5-mini \
  -k 3 \
  -o ./jobs/cache-stampede-results
```

### Using OpenRouter for Models

For models via OpenRouter (GPT-5 mini, other non-Anthropic models):

```yaml
# In job_config.yaml under models:
  - name: "gpt-5-mini"
    provider: "openai"  # Harbor uses OpenAI-compatible interface
    model: "openai/gpt-5-mini"
    api_base: "https://openrouter.ai/api/v1"
    api_key_env: "OPENROUTER_API_KEY"
    extra_headers:
      "HTTP-Referer": "https://harbor.evals"
      "X-Title": "Harbor Cache Stampede Evaluation"
```

**Note**: Harbor uses LiteLLM under the hood for model abstraction. Any OpenAI-compatible endpoint works.

---

## Verifier Design

### Verification Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Harbor Verifier                          │
├─────────────────────────────────────────────────────────────────┤
│  test.sh (entry point)                                          │
│    ├── Runs reconcile_data.py (idempotent)                      │
│    └── Runs pytest tests/test_outputs.py                        │
│         ├── test_cache_stampede_single_flight   ← Core fix      │
│         ├── test_no_memory_leak_in_lock_registry  ← Quality    │
│         ├── test_hard_constraints_unviolated      ← Constraint │
│         ├── test_database_integrity_and_backfill  ← Data fix   │
│         └── test_post_mortem_deliverable          ← Deliverable│
└─────────────────────────────────────────────────────────────────┘
```

### Test Details

| Test | What It Verifies | Failure Mode |
|------|------------------|--------------|
| `test_cache_stampede_single_flight` | 100 concurrent requests → exactly 1 DB query | Stampede not fixed (multiple DB hits) |
| `test_no_memory_leak_in_lock_registry` | Lock dict empty after 100 unique keys | Memory leak (locks accumulate) |
| `test_hard_constraints_unviolated` | `MAX_CONNECTIONS == 5` unchanged | Agent modified db.py |
| `test_database_integrity_and_backfill` | 50 historical records + ≥10 RECONCILED | DB deleted, records lost, backfill missing |
| `test_post_mortem_deliverable` | POST_MORTEM.md exists, ≥80 words, required sections | Missing/incomplete post-mortem |

### Verifier Output

```bash
# test.sh writes to:
/logs/verifier/reward.txt  # Contains "1" (pass) or "0" (fail)

# Harbor collects:
- Trial trajectory (full agent conversation + tool calls)
- Test stdout/stderr
- Reward (0/1)
- Timing metrics
```

### Scoring
- **Binary reward**: 1.0 if ALL 5 tests pass, 0.0 otherwise
- **No partial credit**: Task is all-or-nothing (incident either fully resolved or not)

---

## Limitations

### Known Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| **Single scenario** | Only tests one stampede pattern (cold cache + 100 concurrent) | Add variants: warm cache, gradual load, multi-key |
| **SQLite only** | Doesn't test PostgreSQL/MySQL connection pools | Extend with multi-DB variant |
| **No network partition test** | Redis assumed always available | Add Redis failure scenario |
| **Fixed concurrency** | Always 100 requests | Parametrize concurrency level |
| **No latency SLA** | Only correctness verified, not latency | Add p99 latency threshold test |
| **Deterministic benchmark** | Same key ("metric_1") every run | Randomize key per run |

### Scope Boundaries
- **Does not test**: Distributed locking across multiple app instances (single-process asyncio only)
- **Does not test**: Redis cluster mode, sentinel failover
- **Does not test**: Cache warming strategies, probabilistic early expiration
- **Assumes**: Agent has Python/asyncio/SQLite proficiency (not a language tutorial)

---

## Reproduction Commands

### Local Development (No Harbor)
```bash
# 1. Build container
cd collinear-candidate/distributed-cache-stampede-reconciliation
docker build -t cache-stampede-task ./environment

# 2. Run container
docker run -d --name cache-stampede cache-stampede-task
docker exec -it cache-stampede bash

# 3. Inside container - reproduce bug
python3 /app/scripts/benchmark.py
# Expected: Connection errors / timeout / crash

# 4. Implement fixes (edit files in /app/analytics_service/, /app/scripts/)

# 5. Run reconciliation
python3 /app/scripts/reconcile_data.py

# 6. Write POST_MORTEM.md

# 7. Run tests
pytest /app/tests/test_outputs.py -v
```

### Full Harbor Evaluation
```bash
# 1. Prepare job config (see Model Runs section above)
cat > job_config.yaml << 'EOF'
# ... paste job_config.yaml from above ...
EOF

# 2. Run evaluation
harbor run --config job_config.yaml

# 3. View results
harbor view  # Starts web UI at localhost:8080
# Or analyze locally:
harbor analyze ./jobs/cache-stampede-eval-<timestamp>

# 4. Upload to Harbor Hub (optional)
harbor upload ./jobs/cache-stampede-eval-<timestamp>
```

### Oracle Solution Validation
```bash
# Apply reference implementation
docker cp solution/solve.sh cache-stampede:/app/solution/solve.sh
docker exec cache-stampede bash /app/solution/solve.sh

# Verify
docker exec cache-stampede pytest /app/tests/test_outputs.py -v
# All 5 tests should pass
```

---

## Expected Results Format

### Per-Trial Output (Harbor Standard)
```json
{
  "trial_id": "trial_abc123",
  "task_slug": "collinear-candidate/distributed-cache-stampede-reconciliation",
  "model": "claude-opus-5",
  "reward": 1.0,
  "status": "completed",
  "duration_sec": 245.3,
  "tests": {
    "test_cache_stampede_single_flight": "passed",
    "test_no_memory_leak_in_lock_registry": "passed",
    "test_hard_constraints_unviolated": "passed",
    "test_database_integrity_and_backfill": "passed",
    "test_post_mortem_deliverable": "passed"
  },
  "trajectory_path": "./jobs/.../trial_abc123/trajectory.jsonl"
}
```

### Aggregated Job Results
```
Model              | Attempts | Pass Rate | Avg Time
-------------------|----------|-----------|----------
claude-opus-5      | 3        | 100%      | 180s
claude-sonnet-5    | 3        | 67%       | 210s
gpt-5-mini         | 3        | 33%       | 320s
```

---

## External Sources & Licenses

| Component | Source | License |
|-----------|--------|---------|
| Python 3.11 | python:3.11-slim | PSF |
| Redis 8.x | Debian package | BSD-3-Clause |
| SQLite 3.46 | Debian package | Public Domain |
| redis-py 8.1 | PyPI | MIT |
| pytest 9.1 | PyPI | MIT |
| pytest-asyncio 1.4 | PyPI | Apache-2.0 |
| psutil 7.2 | PyPI | BSD-3-Clause |
| LiteLLM 1.98 | PyPI | MIT |

**Original Work**: This task (incident scenario, buggy code, tests, oracle solution) is original and not derived from any existing benchmark, CTF, tutorial, or prior internal task.

---

## Files Reference

| File | Purpose |
|------|---------|
| `task.toml` | Harbor metadata (difficulty, limits, tags) |
| `instruction.md` | Agent-facing incident description & objectives |
| `README.md` | Human-facing setup & step-by-step guide |
| `RUN_REPORT.md` | This file: fairness, model runs, verifier, limitations |
| `environment/Dockerfile` | Reproducible environment + DB seed |
| `src/analytics_service/cache_manager.py` | **Buggy** - agent must fix |
| `src/analytics_service/audit_reconciler.py` | **Stub** - agent must implement |
| `src/scripts/reconcile_data.py` | **Stub** - agent must implement |
| `solution/solve.sh` | Oracle reference implementation |
| `tests/test_outputs.py` | 5 verification tests |
| `tests/test.sh` | Harbor verifier entry point |