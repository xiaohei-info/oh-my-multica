---
name: software-development-norms
description: Use when writing, reviewing, or refactoring code in any language to apply industrial-grade development norms abstracted from the Alibaba Java Development Handbook. Covers naming, type safety, exceptions, logging, concurrency, databases, project structure, testing, and security with language-agnostic principles and single-language examples.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [coding-standards, naming, concurrency, exceptions, testing, security, engineering-standards, best-practices]
    related_skills: [karpathy-guidelines, clean-architecture, pragmatic-programmer, test-driven-development]
---

# Software Development Norms

> Industrial-grade software engineering rules abstracted from the Alibaba Java Development Handbook (Huangshan Edition). Every principle is language-agnostic; each rule uses a single illustrative example instead of cross-language comparison.

## Overview

The Alibaba Java Development Handbook is a benchmark for enterprise-grade coding standards in China. Its rules are rooted in engineering wisdom — not language-specific syntax. This skill distills those rules into universal principles and warns against common anti-patterns that appear across codebases.

When you write code in any language, load this skill to check your work against battle-tested norms.

## When to Use

- Writing new code in any language and want to follow professional conventions
- Reviewing code for quality, readability, and robustness
- Onboarding onto a project and need to internalize its baseline quality bar
- Refactoring code and want to avoid introducing anti-patterns
- Deciding on naming, error handling, concurrency, or project structure conventions

Do NOT use for:
- Language-specific style guide enforcement (use language linters instead)
- Debating syntax preferences that have no engineering impact

---

## 1. Naming

### 1.1 No abbreviations that obscure meaning

**Principle:** Names must be self-documenting. Never abbreviate by dropping letters arbitrarily — the reader must understand the intent without guessing.

```python
# Good
remaining_retry_count = 3
pull_code_from_remote_repository()

# Bad
rmng_rtry_cnt = 3
a = 3
```


**Anti-pattern:** `AbsClass` for `AbstractClass`, `condi` for `condition`. If you can't say it out loud without explaining the abbreviation, the name is wrong.

### 1.2 Consistent naming conventions per scope

**Principle:** Each scope (package/module, class/struct, method/function, constant, variable) follows a single convention within the project. Mixed conventions create cognitive load.

**Project rule:** Define one naming convention per scope and apply it consistently across the repository.

- Package/module names follow one project-wide convention.
- Type names follow one convention that distinguishes them from functions and values.
- Function/method names follow one convention that matches how the codebase exposes behavior.
- Constant names follow one convention that makes immutable shared values immediately recognizable.
- Variable names follow one convention that stays stable within a file, package, and service boundary.


```rust
// Example — consistent naming
const MAX_STOCK_COUNT: usize = 1000;
struct OrderFactory { ... }
fn calculate_total_price() -> f64 { ... }
```


**Anti-pattern:** Mixing `camelCase` and `snake_case` for variables in the same file; naming a constant `maxCount` instead of `MAX_COUNT`.

### 1.3 Boolean names must be unambiguous

**Principle:** Boolean variables and functions should read as yes/no questions. Avoid negated names — they create double-negative confusion.

```python
# Good
is_deleted = False
has_permission = True
can_retry = True

# Bad
deleted = False       # reads as "deleted is false" — confusing
is_not_valid = True   # double negative when used: if not is_not_valid
```


**Anti-pattern:** Naming a flag `isDeleted` while its serialized field becomes `deleted` due to framework conventions. When the field name and the accessor drift apart, callers will eventually read or write the wrong state.

### 1.4 Reflect design patterns in names

**Principle:** When a class/module implements a known pattern, name it so readers immediately see the intent.

```python
# Example
class OrderFactory: ...
class LoginProxy: ...
class ResourceObserver: ...
```


**Anti-pattern:** Naming a factory `OrderManager`, naming a proxy `LoginHandler` — readers must read the implementation to discover the pattern.

---

## 2. Type Safety and Constants

### 2.1 No magic values

**Principle:** Never embed raw literals in code. Every constant must be named and centralized by domain.

```python
# Good
MAX_RETRY_COUNT = 3
CACHE_KEY_PREFIX = "user_session:"

# Bad
for i in range(3):  # what does 3 mean?
    retry()
cache.set("Id#taobao_" + trade_id, value)  # magic string concatenation
```


**Anti-pattern:** A single `Constants` class/module holding everything. Split by domain: `CacheConsts`, `ConfigConsts`, `ErrorCodes`.

### 2.2 Prefer narrow types and explicit ranges

**Principle:** Use the smallest type that covers the valid range. This prevents invalid states, saves memory, and signals intent.

```python
# Example — use Enum for fixed-range values
from enum import Enum
class Season(Enum):
    SPRING = 1
    SUMMER = 2
    AUTUMN = 3
    WINTER = 4
```


**Anti-pattern:** Using `int` for a boolean flag; using `string` for a status that has exactly 3 valid values.

### 2.3 Use decimal types for money

**Principle:** Floating-point types (`float`, `double`) lose precision. For financial values, always use decimal/fixed-point types.

```python
# Good
from decimal import Decimal
price = Decimal("19.99")
total = price * quantity  # exact arithmetic

# Bad
price = 19.99  # float — 0.1 + 0.2 != 0.3
```


**Anti-pattern:** Storing money in binary floating-point types. The classic `0.1 + 0.2 = 0.30000000000000004` trap is reason enough to avoid them for financial values.

---

## 3. Exception Handling

### 3.1 Never swallow exceptions silently

**Principle:** Catching an exception and doing nothing (or just logging at DEBUG) hides failures. Either handle it, re-throw it, or wrap it with context.

```python
# Good
try:
    result = call_remote_service()
except RemoteServiceError as e:
    logger.error(f"Remote service failed for order {order_id}: {e}")
    raise ServiceUnavailableError(f"Order {order_id} processing failed") from e

# Bad
try:
    result = call_remote_service()
except Exception:
    pass  # silently swallowed
```


**Anti-pattern:** Catching an error and then discarding it. Silent failure paths are bug factories because they erase the only signal that something went wrong.

### 3.2 Specify the narrowest exception type

**Principle:** Catch only the exception types you can meaningfully handle. Broad catches (`Exception`, `error`, `any`) mask bugs and make debugging harder.

```python
# Good
try:
    data = json.loads(raw)
except json.JSONDecodeError as e:
    handle_malformed_input(raw, e)

# Bad
try:
    data = json.loads(raw)
except Exception:  # catches KeyboardInterrupt, MemoryError, etc.
    handle_error()
```


**Anti-pattern:** Catching the broadest possible error type first. Once a catch-all comes before specific cases, the domain-specific recovery path becomes unreachable.

### 3.3 Don't use exceptions for control flow

**Principle:** Exceptions are for exceptional conditions, not for normal branching. Using them for flow control is slow, hard to read, and breaks static analysis.

```python
# Good
if user_id in active_users:
    process(user_id)
else:
    handle_unknown_user(user_id)

# Bad
try:
    process(active_users[user_id])
except KeyError:
    handle_unknown_user(user_id)
```


**Anti-pattern:** Using `KeyError` / `IndexError` as presence checks, or `panic`/`throw` for validation failures that should return error values.

### 3.4 Add context when re-raising or wrapping

**Principle:** When you catch and re-throw, always add information about what you were doing. The original stack trace alone rarely tells the full story.

```python
# Good
try:
    save_to_database(record)
except DatabaseError as e:
    raise ServiceError(f"Failed to save order {record.order_id}") from e
```


**Anti-pattern:** `raise` without a message, or `return err` without wrapping — the caller sees "connection refused" but has no idea which service or operation failed.

---

## 4. Logging

### 4.1 Use the right log level

**Principle:** Each level has a clear contract. Violating it creates alert fatigue or missed incidents.

| Level | When to use | Example |
|-------|-------------|---------|
| ERROR | Requires immediate human attention | Database connection lost, payment failed |
| WARN | Unexpected but recoverable | Retry succeeded after 2 failures, deprecated API called |
| INFO | Business-significant events | Order created, user logged in, batch job completed |
| DEBUG | Diagnostic detail for development | Query parameters, cache hit/miss, loop counters |

```python
# Good
logger.info("Order %s created for user %s, amount=%s", order_id, user_id, amount)
logger.warning("Retry %d/%d succeeded for service %s", attempt, max_retries, service_name)
logger.error("Database connection lost: %s", e, exc_info=True)
```


**Anti-pattern:** Logging normal business flow at ERROR level; using DEBUG for actual errors; logging full stack traces at INFO.

### 4.2 Log with structured context, not just messages

**Principle:** Include identifiers (request ID, user ID, order ID) in every log line. Without them, logs from concurrent requests are inseparable.

```python
# Good
logger.info("Payment processed: order_id=%s user_id=%s amount=%s currency=%s",
            order_id, user_id, amount, currency)

# Bad
logger.info("Payment processed successfully")  # which payment? whose?
```


**Anti-pattern:** "Operation failed" with no identifiers. In a log aggregator, this is useless noise.

### 4.3 Never log sensitive data

**Principle:** Passwords, tokens, credit card numbers, and PII must never appear in logs. Mask or hash them before logging.

```python
# Good
logger.info("User %s authenticated via %s", user_id, auth_method)

# Bad
logger.info("Login attempt: password=%s", password)  # NEVER
logger.debug("API response: %s", response.text)  # might contain tokens
```


**Anti-pattern:** Logging full HTTP responses, dumping request bodies that contain API keys, logging full SSNs "for debugging."

---

## 5. Concurrency

### 5.1 Never create threads/goroutines without a pool

**Principle:** Unbounded thread creation causes OOM and resource exhaustion. Always use a pool with bounded size.

```python
# Good
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=10, thread_name_prefix="order-processor") as pool:
    futures = [pool.submit(process_order, o) for o in orders]
```


**Anti-pattern:** Creating one worker, thread, coroutine, or subprocess per item with no backpressure. That pattern turns traffic spikes into resource-exhaustion incidents.

### 5.2 Lock in consistent order to prevent deadlocks

**Principle:** When acquiring multiple locks, all threads must acquire them in the same order. Different orderings can deadlock.

```python
# Good: always lock A then B
def transfer(from_account, to_account, amount):
    first, second = sorted([from_account.id, to_account.id])
    with lock[first], lock[second]:
        from_account.debit(amount)
        to_account.credit(amount)
```


**Anti-pattern:** Thread 1 locks A then B; Thread 2 locks B then A. Classic deadlock.

### 5.3 Prefer lock-free structures when possible

**Principle:** Locks are expensive. Prefer atomic operations, lock-free data structures, or message-passing patterns.

```python
# Good: use queue for producer-consumer (no explicit locks)
from queue import Queue
task_queue = Queue(maxsize=100)
# producer puts, consumer gets — thread-safe by design
```


**Anti-pattern:** Wrapping every shared variable in a `Mutex` when a channel or atomic would suffice. Over-locking kills throughput.

### 5.4 Be explicit about thread-safety of shared objects

**Principle:** Document whether a type/function is thread-safe. If not, the caller must synchronize. Ambiguity causes data races.

```python
# Example — document thread-safety
class OrderCache:
    """Not thread-safe. Caller must synchronize if shared across threads."""
    def get(self, order_id: str) -> Order | None: ...

class ThreadSafeOrderCache:
    """Thread-safe via internal locking."""
    def __init__(self):
        self._lock = threading.Lock()
        self._cache: dict[str, Order] = {}
    def get(self, order_id: str) -> Order | None:
        with self._lock:
            return self._cache.get(order_id)
```


**Anti-pattern:** Sharing mutable, non-thread-safe objects across concurrent execution paths without synchronization. Races often hide in tests and surface only under production load.

### 5.5 Use optimistic locking for low-contention updates

**Principle:** When concurrent modification is rare, use a version field (optimistic lock) instead of pessimistic locks. Retry on version mismatch.

```python
# Example — optimistic locking
UPDATE_ORDER = """
    UPDATE orders SET status = %s, version = version + 1
    WHERE id = %s AND version = %s
"""
rows = cursor.execute(UPDATE_ORDER, (new_status, order_id, current_version))
if rows == 0:
    raise ConcurrentModificationError(f"Order {order_id} was modified by another transaction")
```


**Anti-pattern:** Always using `SELECT ... FOR UPDATE` (pessimistic lock) when conflict rate is < 20%. It serializes access and kills throughput.

---

## 6. Database

### 6.1 No SELECT * — specify columns explicitly

**Principle:** `SELECT *` wastes I/O, breaks when columns are added/removed, and makes ORM mapping fragile.

```python
# Good
cursor.execute("SELECT id, name, email FROM users WHERE id = %s", (user_id,))

# Bad
cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
```


**Anti-pattern:** `SELECT *` in any query that isn't an ad-hoc investigation.

### 6.2 Use parameterized queries — never concatenate

**Principle:** SQL injection is the #1 most preventable vulnerability. Always use parameterized queries or an ORM that parameterizes.

```python
# Good
cursor.execute("SELECT * FROM users WHERE name = %s", (name,))

# Bad (SQL injection)
cursor.execute(f"SELECT * FROM users WHERE name = '{name}'")
```


**Anti-pattern:** Building SQL with string interpolation or concatenation. Even internal tools are not immune — insiders, admin consoles, and data imports can all become injection paths.

### 6.3 Index on equality columns first, then range

**Principle:** When building a composite index, put equality-condition columns before range-condition columns. The index can't use the range column for ordering if an equality column comes after it.

```sql
-- Good: WHERE a = ? AND b > ? ORDER BY b → index on (a, b)
-- Bad:  WHERE a > ? AND b = ? → b can't use index for lookup
-- Fix:  index on (b, a) so b is first for equality lookup
```


**Anti-pattern:** Indexing `(created_at, status)` when the query is `WHERE status = 'active' AND created_at > ?`. The index can't skip to the status filter first.

### 6.4 No foreign keys in distributed systems

**Principle:** Foreign keys and cascading updates are fine for single-node databases. In distributed/high-concurrency systems, they cause lock escalation, cascade storms, and cross-shard integrity issues. Enforce referential integrity in the application layer.

```python
# Good: application-level integrity check
def delete_user(user_id):
    orders = get_orders_by_user(user_id)
    if orders:
        raise BusinessError("Cannot delete user with existing orders")
    db.delete("users", id=user_id)
```

**Anti-pattern:** `ON DELETE CASCADE` across tables in a microservice architecture. One cascade can lock multiple tables and block writes.

### 6.5 Every table needs audit timestamps

**Principle:** Tables must have `created_at` and `updated_at` columns. They are indispensable for debugging, auditing, and incremental processing.

```sql
-- Good: every table includes these
CREATE TABLE orders (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    -- ... business columns ...
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```


**Anti-pattern:** Tables without `updated_at` — you can never tell when a row was last modified, making debugging and replication debugging impossible.

---

## 7. Project Structure

### 7.1 Layer by responsibility, not by technical type

**Principle:** Organize code by business domain and responsibility (controller, service, repository), not by putting all models in one directory and all controllers in another. The dependency rule: outer layers depend on inner layers, never the reverse.

```
# Good: layered by responsibility
src/
  order/
    controller.py    # HTTP entry point
    service.py       # business logic
    repository.py    # data access
    model.py         # domain model
  user/
    controller.py
    service.py
    repository.py
    model.py

# Bad: grouped by technical type
src/
  controllers/
    order_controller.py
    user_controller.py
  services/
    order_service.py
    user_service.py
  models/
    order.py
    user.py
```

**Anti-pattern:** The "controllers/models/views" layout where you must open 3 directories to understand one feature. This doesn't scale past 10 features.

### 7.2 Keep dependencies minimal and explicit

**Principle:** Every external dependency is a liability. Prefer stdlib; when adding a dependency, document why it's needed and pin its version.

```toml
# Rust — Cargo.toml — explicit, pinned
[dependencies]
serde = { version = "1.0", features = ["derive"] }
tokio = { version = "1.35", features = ["full"] }
```


**Anti-pattern:** Unpinned dependencies (`requests>=2.0`), transitive dependency explosions, importing a 500KB library for one function.

### 7.3 Separate binary, library, and test code

**Principle:** Business logic must be importable without side effects. The `main` function wires dependencies; the library provides the logic; tests exercise the library.

```python
# Good: library code is importable
# order/service.py
def process_order(order: Order, repo: OrderRepository) -> Result:
    ...

# main.py
from order.service import process_order
from order.repository import PostgresOrderRepository

def main():
    repo = PostgresOrderRepository(dsn="...")
    result = process_order(order, repo)

if __name__ == "__main__":
    main()
```


**Anti-pattern:** Business logic inside `main()` or in a file that runs code on import. Tests can't import it without triggering side effects.

---

## 8. Testing

### 8.1 Tests must be AIR: Automatic, Independent, Repeatable

**Principle:** A unit test must run without human intervention, must not depend on other tests, and must produce the same result every time.

```python
# Good: self-contained, no external dependencies
def test_calculate_discount():
    order = Order(total=100, vip_level=2)
    assert calculate_discount(order) == 15

# Bad: depends on database state
def test_get_user():
    user = get_user(42)  # fails if user 42 doesn't exist in test DB
    assert user.name == "Alice"
```


**Anti-pattern:** Tests that depend on execution order, tests that require manual database setup, tests that fail intermittently due to network calls.

### 8.2 Test boundary conditions (BCDE principle)

**Principle:** Test Border values, Correct inputs, Design-aligned scenarios, and Error paths. Missing any of these leaves blind spots.

| Category | What to test | Example |
|----------|-------------|---------|
| Border | Edge values, empty, full, off-by-one | Empty list, max int, first/last element |
| Correct | Happy path with valid inputs | Normal order processes correctly |
| Design | Scenarios aligned with requirements | "VIP users get 20% discount" |
| Error | Invalid inputs, missing resources | Null input, negative quantity, service down |

```python
# Example — BCDE in action
def test_parse_age_border():
    assert parse_age(0) == 0       # minimum
    assert parse_age(150) == 150   # maximum
    with pytest.raises(ValueError):
        parse_age(-1)              # below range
    with pytest.raises(ValueError):
        parse_age(151)             # above range

def test_parse_age_error():
    with pytest.raises(TypeError):
        parse_age("not a number")
```

**Anti-pattern:** Only testing the happy path. Production bugs live in edge cases and error paths.

### 8.3 Core modules must have high coverage

**Principle:** Not all code deserves equal test effort. Core business logic needs 100% branch coverage; glue code and simple CRUD may need less. The 70% line-coverage floor is a minimum, not a target.

```python
# Example — prioritize core logic
# order/pricing.py — CORE, target 100% branch coverage
# order/controller.py — glue, 70% line coverage is acceptable
# order/model.py — data classes, minimal tests needed
```

**Anti-pattern:** 100% line coverage on trivial getters/setters while the pricing engine has 0 branch coverage. Coverage metrics without quality metrics are vanity numbers.

### 8.4 Isolate external dependencies with mocks/stubs

**Principle:** Unit tests must not call external services (databases, APIs, file systems). Use dependency injection and mock/stub the external layer.

```python
# Good: inject dependency, mock in test
class OrderService:
    def __init__(self, payment_gateway: PaymentGateway, repo: OrderRepository):
        self._gateway = payment_gateway
        self._repo = repo

    def process(self, order: Order) -> Result:
        charge = self._gateway.charge(order.total)
        self._repo.save(order.with_charge(charge))
        return Result.success()

# Test
def test_process_order():
    gateway = MagicMock(spec=PaymentGateway)
    gateway.charge.return_value = Charge(amount=100)
    repo = MagicMock(spec=OrderRepository)
    service = OrderService(gateway, repo)
    result = service.process(Order(total=100))
    assert result.is_success()
    gateway.charge.assert_called_once_with(100)
```

**Anti-pattern:** Tests that hit real databases or external APIs. They are slow, flaky, and depend on environment state.

---

## 9. Security

### 9.1 Validate all external input at the boundary

**Principle:** Every input from outside the trust boundary (HTTP request, CLI argument, file, message queue) must be validated before use. Fail fast and fail clearly.

```python
# Good: validate at the boundary
from pydantic import BaseModel, validator

class CreateOrderRequest(BaseModel):
    user_id: str
    amount: float
    currency: str

    @validator("amount")
    def amount_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError("amount must be positive")
        return v
```


**Anti-pattern:** Trusting input from "internal" services without validation. Internal services get compromised too.

### 9.2 Never expose internal errors to clients

**Principle:** Error responses must not leak stack traces, SQL queries, file paths, or internal system details. Return a generic error with a correlation ID; log the details server-side.

```python
# Good
@app.errorhandler(Exception)
def handle_error(e):
    correlation_id = str(uuid4())
    logger.error("Unhandled error [correlation_id=%s]: %s", correlation_id, e, exc_info=True)
    return {"error": "Internal server error", "correlation_id": correlation_id}, 500

# Bad
@app.errorhandler(Exception)
def handle_error(e):
    return {"error": str(e), "traceback": traceback.format_exc()}, 500  # leaks internals
```

**Anti-pattern:** Returning `str(exception)` to the client, exposing Django/Flask debug pages in production, or sending stack traces in API responses.

### 9.3 Sensitive data must be encrypted at rest and in transit

**Principle:** Passwords are hashed (never reversed). PII is encrypted. All network communication uses TLS. Credentials are never in source code.

```python
# Good: bcrypt for passwords
import bcrypt
hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12))
# verify: bcrypt.checkpw(input.encode(), hashed)

# Bad
import hashlib
hashed = hashlib.md5(password.encode()).hexdigest()  # reversible with rainbow tables
```


**Anti-pattern:** MD5/SHA1 for passwords, storing API keys in `.env` files committed to git, using HTTP for internal services "because they're behind the firewall."

### 9.4 Principle of least privilege

**Principle:** Services, containers, and users should have the minimum permissions needed to function. A compromised service with root access is a catastrophic breach; the same service with read-only DB access is a contained incident.

```yaml
# Docker — good: drop all capabilities, add only what's needed
securityContext:
  runAsNonRoot: true
  readOnlyRootFilesystem: true
  capabilities:
    drop: ["ALL"]
    add: ["NET_BIND_SERVICE"]
```


**Anti-pattern:** Running containers as root, using `GRANT ALL` for application database users, deploying with admin credentials.

---

## 10. Control Flow and Code Quality

### 10.1 Guard clauses over nested if-else

**Principle:** Avoid deep nesting. Return early for error/edge cases (guard clauses). This keeps the happy path at the leftmost indentation.

```python
# Good: guard clauses
def process_order(order):
    if not order:
        raise ValueError("order is required")
    if order.is_cancelled():
        return Result.skipped("cancelled order")
    if not order.has_items():
        return Result.skipped("empty order")
    # happy path at leftmost indentation
    total = calculate_total(order)
    charge(total)
    return Result.success()

# Bad: nested if-else
def process_order(order):
    if order:
        if not order.is_cancelled():
            if order.has_items():
                total = calculate_total(order)
                charge(total)
                return Result.success()
            else:
                return Result.skipped("empty order")
        else:
            return Result.skipped("cancelled order")
    else:
        raise ValueError("order is required")
```


**Anti-pattern:** More than 3 levels of if-else nesting. If you reach 3, refactor with guard clauses, strategy pattern, or state pattern.

### 10.2 Avoid negated conditions

**Principle:** Positive conditions are easier to understand. `if is_valid` beats `if !is_invalid`. Double-negatives (`if !is_not_found`) require mental gymnastics.

```python
# Good
if user.is_active():
    process(user)

# Bad
if not user.is_inactive():
    process(user)
```


**Anti-pattern:** `if !(x >= threshold)` instead of `if x < threshold`. The reader must negate the negation mentally.

### 10.3 Always use braces/blocks for control flow

**Principle:** Even single-line bodies must use braces/blocks. This prevents bugs when adding lines later and makes diffs cleaner.

```python
# Example — indentation-based, always uses blocks naturally
if condition:
    do_something()  # adding a line here is safe
```


**Anti-pattern:** Using single-line control-flow bodies without an explicit block. The next edit often adds a second line and silently changes which statements are conditional.

---

## Common Anti-Patterns Summary

| Anti-Pattern | Why It Hurts | Where It Appears |
|-------------|-------------|-----------------|
| Magic numbers/strings | Unmaintainable, error-prone | All languages |
| Swallowed exceptions | Silent failures in production | All languages |
| `SELECT *` | Fragile, wasteful, slow | SQL in any language |
| Unbounded thread creation | OOM, resource exhaustion | All languages |
| Deep nesting (3+ levels) | Unreadable, bug-prone | All languages |
| Negated conditions | Double-negatives confuse readers | All languages |
| No audit timestamps | Impossible to debug data issues | Databases |
| SQL concatenation | Injection vulnerability | All languages |
| Leaking stack traces | Security exposure | All languages |
| Tests depending on external state | Flaky, non-repeatable tests | All languages |
| Single Constants file | Unmaintainable, coupling | All languages |
| Foreign keys in distributed systems | Lock storms, cross-shard issues | Databases |

---

## Verification Checklist

Before submitting code, verify:

- [ ] **Naming:** All names are self-documenting; no abbreviations that require explanation; boolean names are positive; design patterns reflected in names
- [ ] **Type Safety:** No magic values; constants are grouped by domain; money uses decimal types; enums for fixed ranges
- [ ] **Exceptions:** No swallowed exceptions; narrow catch types; no exceptions for control flow; re-raised exceptions carry context
- [ ] **Logging:** Correct log levels; structured context with IDs; no sensitive data in logs
- [ ] **Concurrency:** Threads created via pools; locks acquired in consistent order; lock-free structures preferred where possible; thread-safety documented
- [ ] **Database:** No `SELECT *`; parameterized queries only; composite indexes ordered correctly (equality first); no FKs in distributed systems; audit timestamps present
- [ ] **Structure:** Layered by responsibility; minimal explicit dependencies; binary/library/test separation
- [ ] **Testing:** AIR compliant (Automatic, Independent, Repeatable); BCDE coverage; core modules have high branch coverage; external deps mocked
- [ ] **Security:** Input validated at boundary; no internal error leakage; sensitive data encrypted; least privilege applied
- [ ] **Control Flow:** Guard clauses over nesting; positive conditions; braces/blocks always used

---

## Source

Abstracted from the Alibaba Java Development Handbook (Huangshan Edition, 2022) — the p3c project on GitHub. The original handbook is Java-specific; this skill preserves the engineering rationale while restating the rules as general software-development norms.

