---
description: Type class pattern (Functor, Applicative, Monad, custom classes)
language: haskell
task: coding
version: 1.5.0
---

# Example: Type Classes in Haskell

## Scenario
Build a `Logger` type class that supports different logging backends (console, file, in-memory).

## Pattern 1: Define a type class with laws

```haskell
{-# LANGUAGE ConstraintKinds #-}

class Monad m => MonadLogger m where
  logMsg :: LogLevel -> Text -> m ()

data LogLevel = Debug | Info | Warn | Error deriving (Eq, Show)

-- Default implementation (via default signatures or helper functions)
logInfo :: MonadLogger m => Text -> m ()
logInfo = logMsg Info

logError :: MonadLogger m => Text -> m ()
logError = logMsg Error
```

## Pattern 2: Multiple instances

```haskell
import qualified Data.IORef as IORef

-- Instance 1: Console logger (IO-based)
newtype ConsoleLogger a = ConsoleLogger { runConsoleLogger :: IO a }
  deriving newtype (Functor, Applicative, Monad, MonadIO)

instance MonadLogger ConsoleLogger where
  logMsg level msg = ConsoleLogger $ do
    putStrLn $ "[" <> show level <> "] " <> unpack msg

-- Instance 2: In-memory logger (for testing)
newtype MemoryLogger a = MemoryLogger { runMemoryLogger :: IORef [LogEntry] -> IO a }
  deriving newtype (Functor, Applicative, Monad)

data LogEntry = LogEntry LogLevel Text deriving (Show)

instance MonadLogger MemoryLogger where
  logMsg level msg = MemoryLogger $ \ref ->
    IORef.modifyIORef' ref (LogEntry level msg :)
```

## Pattern 3: Polymorphic code

```haskell
-- Works with ANY MonadLogger, not just Console or Memory
doWork :: MonadLogger m => m ()
doWork = do
  logInfo "Starting work"
  -- ... do stuff
  logInfo "Work complete"

-- Usage:
main = runConsoleLogger doWork        -- prints to stdout

testWork :: IO [LogEntry]
testWork = do
  ref <- IORef.newIORef []
  runMemoryLogger doWork ref
  reverse <$> IORef.readIORef ref
```

## Pattern 4: Laws and property tests

```haskell
-- Functor laws
prop_functor_id :: [Int] -> Bool
prop_functor_id xs = fmap id xs == xs

prop_functor_compose :: [Int] -> Bool
prop_functor_compose xs = fmap (f . g) xs == (fmap f . fmap g) xs
  where
    f = (* 2)
    g = (+ 1)
```

## Anti-patterns

```haskell
-- DON'T: orphan instances (instance defined in different module than type/class)
-- module Foo
-- instance Show Bar where ...  -- Bar defined in another module

-- DO: define instance in same module as type OR class

-- DON'T: abuse type classes for overloading unrelated operations
class DoStuff a where
  doStuff :: a -> a
-- This is too vague; use a concrete function name instead.

-- DON'T: too many type class parameters (MultiParamTypeClasses)
class C a b c d where ...  -- hard to infer, hard to use

-- DON'T: lawless type classes (no invariants)
class Save a where
  save :: a -> IO ()
-- What does `save` mean? Add laws: save x >> load == Just x
```

## Key Takeaways
- Type classes encode shared behavior across types
- Always document laws (e.g., `fmap id == id` for Functor)
- Use `newtype` to define instances for existing types
- Avoid orphan instances — define in same module as type or class
- Property test your instances against laws
- Prefer concrete types at module boundaries; polymorphism inside
