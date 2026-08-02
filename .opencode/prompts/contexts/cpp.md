---
description: C++ conventions (modern C++17/20, RAII, templates)
language: cpp
priority: 80
version: 1.5.0
---

# C++ Context

## Style
- Follow Google C++ Style Guide or ISO C++ Core Guidelines
- snake_case for functions/variables, PascalCase for classes/structs, UPPER_SNAKE for constants
- 2-space indentation (Google) or 4-space (ISO)
- Maximum line length: 100 chars
- `#pragma once` for header guards (preferred over `#ifndef`)

## Project Conventions
- C++17 minimum, C++20 preferred (concepts, ranges, coroutines)
- Build: CMake 3.20+ with `target_*` commands (not global `include_directories`)
- Testing: Google Test (gtest) + Google Mock (gmock)
- Compiler: MSVC 2022+ / GCC 12+ / Clang 14+
- Static analysis: clang-tidy, cppcheck

## Memory Management
- RAII: every resource (memory, file, lock) owned by an object
- Prefer `std::unique_ptr<T>` for exclusive ownership
- Use `std::shared_ptr<T>` only for shared ownership (avoid by design if possible)
- Never use `new` / `delete` directly — use `std::make_unique` / `std::make_shared`
- Use `std::vector` / `std::string` / `std::array` instead of C arrays

## Modern Features (C++17/20)
- `auto` for type deduction (but not for templates where type matters)
- `std::optional<T>` for nullable values
- `std::variant<A, B>` for type-safe unions
- Structured bindings: `auto [a, b] = pair;`
- Range-based for loops: `for (const auto& x : container)`
- `constexpr` / `consteval` for compile-time computation

## Common Patterns
- `std::move` for rvalue references; `std::forward` for perfect forwarding
- Concepts (C++20) for constraining templates
- Coroutines (C++20) for async code
- `std::expected<T, E>` (C++23) or `tl::expected` for error handling
- Templates over inheritance for static polymorphism

## Anti-patterns
- Raw pointers with manual `delete` → smart pointers
- `printf` / `scanf` → `std::cout` / `std::cin` or `fmt::format`
- C-style casts → `static_cast` / `dynamic_cast` / `reinterpret_cast`
- Macros for constants → `constexpr`
- Virtual functions when CRTP or templates suffice

## Testing
- Test files: `*_test.cpp` in `tests/` directory
- Use `TEST()`, `TEST_F()` for fixtures
- `EXPECT_*` for non-fatal, `ASSERT_*` for fatal assertions
- Google Mock for mocking interfaces
- Use `gtest_main` target to avoid writing `main()`
