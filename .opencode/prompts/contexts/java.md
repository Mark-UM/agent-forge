---
description: Java + JVM ecosystem conventions
language: java
priority: 80
version: 1.5.0
---

# Java Context

## Style
- Follow Google Java Style Guide
- camelCase for methods/variables, PascalCase for classes/interfaces, UPPER_SNAKE for constants
- 4-space indentation
- One statement per line, one declaration per line
- Braces: K&R style (open on same line for blocks)

## Project Conventions
- Java 17+ LTS (prefer 21 LTS)
- Build: Maven or Gradle (Kotlin DSL preferred)
- Testing: JUnit 5 + AssertJ + Mockito
- Logging: SLF4J + Logback (never `System.out.println` in production code)
- HTTP: `java.net.http.HttpClient` (JDK 11+) or Spring `RestClient`

## Type System
- Prefer `record` for immutable data carriers (Java 16+)
- Use `sealed` interfaces for closed hierarchies (Java 17+)
- Prefer `Optional<T>` for return types that may be absent (never as field type)
- Use `var` for local variables when type is obvious
- Avoid raw types — use generics

## Common Patterns
- Streams API for collection operations (map/filter/reduce/collect)
- `try-with-resources` for AutoCloseable
- `CompletableFuture` for async composition
- Pattern matching for switch (Java 21+) and instanceof (Java 16+)
- Dependency injection: constructor injection (avoid field injection)

## Anti-patterns
- `null` returns → `Optional<T>`
- Mutable shared state → immutability + message passing
- `instanceof` chains → sealed interfaces + pattern matching
- Checked exceptions for control flow → unchecked exceptions
- `Date` / `Calendar` → `java.time` API

## Testing
- Test files: `*Test.java` in `src/test/java/`
- Use `@Test`, `@BeforeEach`, `@AfterEach`
- AssertJ for fluent assertions (`assertThat(x).isEqualTo(y)`)
- Mockito for mocking (`@Mock`, `@InjectMocks`)
- Test containers for integration tests with real DB/services
