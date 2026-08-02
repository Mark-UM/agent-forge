---
description: API endpoint example (Express, validation, error handling)
language: typescript
task: coding
version: 1.5.0
---

# Example: API Endpoint (Express + TypeScript)

## Scenario
Build a `POST /users` endpoint with validation, error handling, and typed response.

## Implementation

```ts
// routes/users.ts
import { Router, type Request, type Response } from 'express';
import { z } from 'zod';

const router = Router();

const CreateUserSchema = z.object({
  email: z.string().email(),
  name: z.string().min(1).max(100),
  age: z.number().int().min(0).max(150).optional(),
});

type CreateUserInput = z.infer<typeof CreateUserSchema>;

interface User {
  id: string;
  email: string;
  name: string;
  age?: number;
  createdAt: string;
}

router.post('/users', async (req: Request, res: Response) => {
  // 1. Validate input
  const parseResult = CreateUserSchema.safeParse(req.body);
  if (!parseResult.success) {
    return res.status(400).json({
      error: 'VALIDATION_ERROR',
      details: parseResult.error.flatten(),
    });
  }

  const input: CreateUserInput = parseResult.data;

  try {
    // 2. Business logic
    const user: User = await createUser(input);

    // 3. Success response
    return res.status(201).json(user);
  } catch (err) {
    if (err instanceof EmailAlreadyExistsError) {
      return res.status(409).json({ error: 'EMAIL_EXISTS' });
    }
    // Don't leak internal errors to client
    console.error('Failed to create user:', err);
    return res.status(500).json({ error: 'INTERNAL_ERROR' });
  }
});

export { router as usersRouter };
```

## Test

```ts
// routes/users.test.ts
import request from 'supertest';
import { app } from '../app';

describe('POST /users', () => {
  test('creates user with valid input', async () => {
    const res = await request(app)
      .post('/users')
      .send({ email: 'test@example.com', name: 'Test' })
      .expect(201);

    expect(res.body).toMatchObject({
      email: 'test@example.com',
      name: 'Test',
    });
    expect(res.body.id).toBeDefined();
  });

  test('rejects invalid email', async () => {
    const res = await request(app)
      .post('/users')
      .send({ email: 'not-an-email', name: 'Test' })
      .expect(400);

    expect(res.body.error).toBe('VALIDATION_ERROR');
  });
});
```

## Anti-patterns

```ts
// DON'T: trust req.body without validation
router.post('/users', (req, res) => {
  const { email, name } = req.body;  // could be anything!
});

// DON'T: leak internal errors
catch (err) {
  res.status(500).json({ error: err.message });  // leaks stack/info
}

// DON'T: use `any` for response
function createUser(input: any): any { ... }

// DON'T: forget to type the schema
const schema = z.object({ email: z.string() });  // not email-validated
```

## Key Takeaways
- Validate ALL inputs at the boundary (zod / valibot / io-ts)
- Use `safeParse` to avoid throwing on validation errors
- Never leak internal errors — return generic messages + log details
- Type inputs and outputs explicitly (no `any`)
- HTTP status codes: 201 Created, 400 Bad Request, 409 Conflict, 500 Internal
