---
description: Security review checklist (input validation, auth, secrets)
language: neutral
task: review
version: 1.5.0
---

# Example: Security Review Checklist

## Categories to check

### 1. Input Validation
- [ ] All external input validated at boundary (schema, type, length)
- [ ] SQL queries parameterized (no string concatenation)
- [ ] Shell commands use `shell=False` / `execve` (no shell injection)
- [ ] File paths validated against traversal (`../`)
- [ ] JSON/XML parsed with safe parsers (no `eval` / `xmllibetree`)

### 2. Authentication & Authorization
- [ ] Passwords hashed with bcrypt/argon2 (not MD5/SHA1)
- [ ] Auth tokens stored securely (httpOnly cookies, not localStorage)
- [ ] Permission checks on EVERY protected route
- [ ] CSRF tokens for state-changing operations
- [ ] Rate limiting on login / password reset

### 3. Secrets Management
- [ ] No API keys / passwords in source code
- [ ] Secrets loaded from env vars or secret manager
- [ ] `.env` files in `.gitignore`
- [ ] Logs redact secrets (no Authorization headers, no token bodies)
- [ ] Error messages don't leak internal state

### 4. Injection Prevention
- [ ] SQL: parameterized queries (`cursor.execute("... %s", (val,))`)
- [ ] Command injection: `subprocess.run(["cmd", arg], shell=False)`
- [ ] XSS: user input escaped before rendering (React escapes by default)
- [ ] Path traversal: validate `Path(p).resolve()` is within allowed root
- [ ] SSRF: validate outbound URLs against allowlist

### 5. Cryptography
- [ ] Use TLS for all network communication
- [ ] Don't roll your own crypto — use `cryptography` / `openssl`
- [ ] Random values from `secrets` module (not `random`)
- [ ] JWT signatures verified (don't trust `alg: none`)

### 6. Error Handling
- [ ] No stack traces leaked to users
- [ ] Generic error messages for clients
- [ ] Detailed errors logged server-side
- [ ] No sensitive data in exception messages

## Common Findings

### Critical
- Hardcoded API key in source
- SQL query built with f-string
- `eval(user_input)` anywhere
- Password stored in plaintext
- `shell=True` with user input

### High
- Missing auth check on a route
- CSRF token not validated
- JWT signature not verified
- Path traversal allowed in file upload
- Rate limiting absent on auth endpoints

### Medium
- Verbose error messages (reveal stack)
- Cookies without `Secure` / `HttpOnly` flags
- Secrets in logs
- Missing TLS in dev / staging
- Insecure deserialization (pickle, yaml.load without SafeLoader)

### Low
- Information disclosure in headers (Server, X-Powered-By)
- Verbose logging in production
- Missing Content-Security-Policy
- Cookies without `SameSite` attribute

## Review Process
1. List all external inputs (HTTP, env, files, CLI args)
2. Trace each input through the codebase
3. Verify validation at each trust boundary
4. Check for dangerous sinks (eval, system, SQL, open, exec)
5. Review auth flows end-to-end
6. Verify secrets handling
7. Check error paths for information leakage
