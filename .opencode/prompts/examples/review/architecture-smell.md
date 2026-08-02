---
description: Architecture smell review (coupling, cohesion, layering)
language: neutral
task: review
version: 1.5.0
---

# Example: Architecture Smell Review

## Categories to check

### 1. Layering Violations
- [ ] UI layer doesn't directly access database
- [ ] Business logic doesn't import HTTP / framework types
- [ ] Data layer doesn't contain business rules
- [ ] Cross-layer imports follow the dependency rule (outer → inner, never reverse)

### 2. Coupling
- [ ] No "god classes" / "god modules" (knows too much, does too much)
- [ ] Modules communicate via interfaces, not concrete types
- [ ] No circular dependencies (A imports B, B imports A)
- [ ] Shared mutable state minimized (prefer pure functions / message passing)

### 3. Cohesion
- [ ] Each module has a single clear responsibility
- [ ] Functions in a module work toward the same purpose
- [ ] No "utility" modules dumping grounds (split by domain)
- [ ] No "misc" / "helpers" / "common" folders without clear scope

### 4. Abstraction
- [ ] Abstractions match the domain (not technical leakiness)
- [ ] No "leaky abstractions" (e.g., exposing SQL errors through service layer)
- [ ] No premature abstraction (YAGNI — abstractions for hypothetical needs)
- [ ] No copy-paste duplication (extract, but only after 3+ instances)

### 5. Dependency Direction
- [ ] Domain layer depends on nothing (pure business logic)
- [ ] Application layer depends on domain
- [ ] Infrastructure layer depends on application (via interfaces)
- [ ] No domain entity imports framework / library types

## Common Findings

### Critical (Architectural violation)
```python
# DON'T: UI directly queries database
@app.route("/users")
def list_users():
    rows = db.execute("SELECT * FROM users")  # UI → DB, skips all layers
    return render_template("users.html", users=rows)

# DO: route → service → repository → db
@app.route("/users")
def list_users():
    users = user_service.list_users()  # delegate to service
    return render_template("users.html", users=users)
```

### High (God module)
```python
# DON'T: utils.py with 50 unrelated functions
# utils.py
def format_date(d): ...
def send_email(to, body): ...
def calculate_tax(amount): ...
def hash_password(pw): ...
def parse_csv(data): ...

# DO: split by domain
# formatting.py: format_date
# email.py: send_email
# tax.py: calculate_tax
# auth.py: hash_password
# csv_utils.py: parse_csv
```

### High (Circular dependency)
```
# DON'T: A imports B, B imports A
# a.py
from b import do_b
def do_a(): do_b()

# b.py
from a import do_a  # circular!
def do_b(): do_a()

# DO: extract shared dependency, or use dependency inversion
# shared.py (no imports from a or b)
# a.py imports shared
# b.py imports shared
```

### Medium (Leaky abstraction)
```python
# DON'T: service exposes SQL errors
class UserService:
    def get_user(self, id):
        return self.db.execute("SELECT * FROM users WHERE id = %s", id)
        # caller might get SQLAlchemyError — service should wrap

# DO: service translates to domain error
class UserService:
    def get_user(self, id) -> User | None:
        try:
            return self.db.execute(...)
        except DBError as e:
            log.warning("DB error fetching user", e)
            return None  # or raise UserNotFound
```

### Medium (Premature abstraction)
```python
# DON'T: interface for one implementation
class IUserRepository(ABC):  # only one impl: UserRepository
    @abstractmethod
    def get(self, id): ...

class UserRepository(IUserRepository):
    def get(self, id): return self.db.query(...)

# DO: define abstraction only when there are 2+ implementations
class UserRepository:
    def get(self, id): return self.db.query(...)
# Add interface later when a second impl (e.g., MockUserRepository) appears
```

### Low (Misc folder smell)
```
# DON'T: misc/ or helpers/ dumping ground
project/
├── misc/
│   ├── date_utils.py
│   ├── string_utils.py
│   ├── network_utils.py
│   └── file_utils.py

# DO: organize by domain
project/
├── time/
│   └── formatting.py
├── strings/
│   └── transforms.py
├── net/
│   └── http_client.py
└── fs/
    └── paths.py
```

## Review Process
1. Draw module dependency graph (identify cycles)
2. Check layer boundaries (UI / service / data)
3. List "god modules" — measure by import count and LOC
4. Identify leaky abstractions (lower-layer types leaking up)
5. Find duplication (3+ similar functions = extract candidate)
6. Check for premature abstraction (interfaces with 1 impl)
