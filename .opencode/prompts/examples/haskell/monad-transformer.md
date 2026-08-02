---
description: Monad transformer pattern (ExceptT, ReaderT)
language: haskell
task: coding
version: 1.5.0
---

# Example: Monad Transformers in Haskell

## Scenario
Build a function that reads config from environment, validates it, and may fail with typed errors.

## Without transformers (nested case)

```haskell
data ConfigError
  = MissingEnv String
  | InvalidFormat String

readConfig :: IO (Either ConfigError Config)
readConfig = do
  hostStr <- lookupEnv "HOST"
  case hostStr of
    Nothing -> return (Left (MissingEnv "HOST"))
    Just host -> do
      portStr <- lookupEnv "PORT"
      case portStr of
        Nothing -> return (Left (MissingEnv "PORT"))
        Just portStr' -> case readMaybe portStr' of
          Nothing -> return (Left (InvalidFormat "PORT"))
          Just port -> return (Right (Config host port))
```

Nested `case` is hard to read. Use `ExceptT` to flatten:

## With ExceptT (flattened)

```haskell
{-# LANGUAGE LambdaCase #-}

import Control.Monad.Except
import System.Environment (lookupEnv)
import Text.Read (readMaybe)

data ConfigError
  = MissingEnv String
  | InvalidFormat String
  deriving (Show)

data Config = Config
  { cfgHost :: String
  , cfgPort :: Int
  } deriving (Show)

type App = ExceptT ConfigError IO

readConfig :: App Config
readConfig = do
  host <- readEnv "HOST"
  portStr <- readEnv "PORT"
  port <- liftEither $ maybe (Left (InvalidFormat "PORT")) Right (readMaybe portStr)
  return (Config host port)
  where
    readEnv :: String -> App String
    readEnv name = do
      mVal <- liftIO (lookupEnv name)
      maybe (throwError (MissingEnv name)) return mVal

-- Run the App
loadConfig :: IO (Either ConfigError Config)
loadConfig = runExceptT readConfig
```

## Pattern: ReaderT for dependency injection

```haskell
type App env = ReaderT env (ExceptT AppError IO)

askConfig :: App Config String
askConfig = asks cfgHost  -- ReaderT's `asks`

withEnv :: env -> App env a -> IO (Either AppError a)
withEnv env action = runExceptT (runReaderT action env)
```

## Anti-patterns

```haskell
-- DON'T: throw exceptions in pure code
readConfig :: IO Config
readConfig = do
  host <- lookupEnv "HOST"
  case host of
    Nothing -> error "HOST not set"  -- impure exception, hard to catch
    Just h  -> return (Config h)

-- DON'T: stack transformers in wrong order
type App = ReaderT Config (ExceptT Error IO)
-- vs
type App = ExceptT Error (ReaderT Config IO)
-- The first is usually what you want; order matters for semantics.

-- DON'T: liftIO everywhere (defeats the purpose)
action = do
  liftIO $ putStrLn "hi"
  liftIO $ readFile "x"
  liftIO $ ...  -- just use IO at this point
```

## Key Takeaways
- `ExceptT e m a` = `m (Either e a)` — flatten nested `Either` in `do` notation
- `ReaderT r m a` = `r -> m a` — pass config implicitly
- Stack transformers with the "outermost effect last" rule of thumb
- `throwError` for typed errors; `liftIO` to embed `IO` actions
- `runExceptT` / `runReaderT` to peel layers
