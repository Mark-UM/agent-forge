"""Contract tests for modules.common.run — unified Run/Step/Event model (Phase C).

Covers:
    * Enum contracts (RunStatus, EventType, EventLevel values)
    * Dataclass shape (Event, Step, Run, RunResult fields and defaults)
    * Serialization (to_dict roundtrips)
    * RunRecorder: step recording, event emission, status derivation
    * StepRecorder: terminal transitions (succeed/degrade/skip/fail/time_out)
    * Auto-completion on context exit (success + exception paths)
    * Exception propagation (step fails, run fails, exception re-raised)
    * Listener notification (events delivered in real time)
    * Adapter: run_from_step_reports
    * RunResult.from_run
    * Invariants: events are append-only; status derivation rules
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from modules.common.result import OperationResult, StepStatus
from modules.common.run import (
    Event, EventLevel, EventType, Run, RunRecorder, RunResult, RunStatus,
    Step, StepRecorder, run_from_step_reports,
)


# ── Enum contract tests ─────────────────────────────────────

class TestRunStatus:
    def test_values_match_step_status(self):
        """RunStatus uses the same string values as StepStatus for shared states."""
        assert RunStatus.PENDING == StepStatus.PENDING.value
        assert RunStatus.RUNNING == StepStatus.RUNNING.value
        assert RunStatus.SUCCEEDED == StepStatus.SUCCEEDED.value
        assert RunStatus.DEGRADED == StepStatus.DEGRADED.value
        assert RunStatus.FAILED == StepStatus.FAILED.value
        assert RunStatus.TIMED_OUT == StepStatus.TIMED_OUT.value
        assert RunStatus.ABANDONED == StepStatus.ABANDONED.value

    def test_is_terminal(self):
        assert RunStatus.is_terminal(RunStatus.SUCCEEDED)
        assert RunStatus.is_terminal(RunStatus.DEGRADED)
        assert RunStatus.is_terminal(RunStatus.FAILED)
        assert RunStatus.is_terminal(RunStatus.TIMED_OUT)
        assert RunStatus.is_terminal(RunStatus.ABANDONED)
        assert not RunStatus.is_terminal(RunStatus.PENDING)
        assert not RunStatus.is_terminal(RunStatus.RUNNING)

    def test_is_success_like(self):
        assert RunStatus.is_success_like(RunStatus.SUCCEEDED)
        assert RunStatus.is_success_like(RunStatus.DEGRADED)
        assert not RunStatus.is_success_like(RunStatus.FAILED)
        assert not RunStatus.is_success_like(RunStatus.PENDING)


class TestEventType:
    def test_run_events(self):
        assert EventType.RUN_STARTED == 'run.started'
        assert EventType.RUN_COMPLETED == 'run.completed'
        assert EventType.RUN_FAILED == 'run.failed'

    def test_step_events(self):
        assert EventType.STEP_STARTED == 'step.started'
        assert EventType.STEP_COMPLETED == 'step.completed'
        assert EventType.STEP_FAILED == 'step.failed'
        assert EventType.STEP_SKIPPED == 'step.skipped'

    def test_cache_events(self):
        assert EventType.CACHE_HIT == 'cache.hit'
        assert EventType.CACHE_MISS == 'cache.miss'
        assert EventType.CACHE_STORE == 'cache.store'

    def test_provider_events(self):
        assert EventType.PROVIDER_CALLED == 'provider.called'
        assert EventType.PROVIDER_RESULT == 'provider.result'
        assert EventType.PROVIDER_ERROR == 'provider.error'

    def test_custom_exists(self):
        assert EventType.CUSTOM == 'custom'


class TestEventLevel:
    def test_levels(self):
        assert EventLevel.DEBUG == 'debug'
        assert EventLevel.INFO == 'info'
        assert EventLevel.WARN == 'warn'
        assert EventLevel.ERROR == 'error'


# ── Dataclass shape tests ───────────────────────────────────

class TestEventDataclass:
    def test_defaults(self):
        e = Event(event_id='e1', run_id='r1', timestamp='2026-01-01T00:00:00+00:00',
                  type=EventType.RUN_STARTED)
        assert e.level == EventLevel.INFO
        assert e.step_id is None
        assert e.step_name is None
        assert e.payload == {}

    def test_to_dict_roundtrip(self):
        e = Event(
            event_id='e1', run_id='r1', timestamp='2026-01-01T00:00:00+00:00',
            type=EventType.CACHE_HIT, level=EventLevel.INFO,
            step_id='s1', step_name='cache_lookup',
            payload={'key': 'abc'},
        )
        d = e.to_dict()
        assert d['event_id'] == 'e1'
        assert d['run_id'] == 'r1'
        assert d['type'] == 'cache.hit'
        assert d['level'] == 'info'
        assert d['step_id'] == 's1'
        assert d['step_name'] == 'cache_lookup'
        assert d['payload'] == {'key': 'abc'}


class TestStepDataclass:
    def test_defaults(self):
        s = Step(step_id='s1', run_id='r1', name='validate', order=0)
        assert s.status == RunStatus.PENDING
        assert s.started_at is None
        assert s.ended_at is None
        assert s.duration_ms == 0
        assert isinstance(s.result, OperationResult)
        assert s.result.status == StepStatus.PENDING

    def test_to_dict_contains_result(self):
        s = Step(
            step_id='s1', run_id='r1', name='validate', order=0,
            status=RunStatus.SUCCEEDED,
            result=OperationResult.success_with(data={'q': 'test'}),
        )
        d = s.to_dict()
        assert d['step_id'] == 's1'
        assert d['name'] == 'validate'
        assert d['order'] == 0
        assert d['status'] == 'succeeded'
        assert d['result']['success'] is True
        assert d['result']['data'] == {'q': 'test'}


class TestRunDataclass:
    def test_defaults(self):
        r = Run(run_id='r1', run_type='search')
        assert r.status == RunStatus.PENDING
        assert r.steps == []
        assert r.events == []
        assert r.metadata == {}
        assert r.error is None

    def test_success_property(self):
        r = Run(run_id='r1', run_type='search', status=RunStatus.SUCCEEDED)
        assert r.success is True
        r.status = RunStatus.FAILED
        assert r.success is False

    def test_degraded_mode_property(self):
        r = Run(run_id='r1', run_type='search', status=RunStatus.SUCCEEDED)
        assert r.degraded_mode is False
        r.status = RunStatus.DEGRADED
        assert r.degraded_mode is True
        # Degraded step also triggers
        r.status = RunStatus.SUCCEEDED
        r.steps.append(Step(step_id='s1', run_id='r1', name='plan', order=0,
                            status=RunStatus.DEGRADED))
        assert r.degraded_mode is True

    def test_step_by_name(self):
        r = Run(run_id='r1', run_type='search')
        r.steps.append(Step(step_id='s1', run_id='r1', name='validate', order=0))
        r.steps.append(Step(step_id='s2', run_id='r1', name='plan', order=1))
        assert r.step_by_name('plan').step_id == 's2'
        assert r.step_by_name('nonexistent') is None

    def test_events_for_step(self):
        r = Run(run_id='r1', run_type='search')
        r.events.append(Event(event_id='e1', run_id='r1', timestamp='t',
                              type=EventType.STEP_STARTED, step_id='s1'))
        r.events.append(Event(event_id='e2', run_id='r1', timestamp='t',
                              type=EventType.RUN_STARTED, step_id=None))
        r.events.append(Event(event_id='e3', run_id='r1', timestamp='t',
                              type=EventType.STEP_COMPLETED, step_id='s1'))
        step_events = r.events_for_step('s1')
        assert len(step_events) == 2
        assert all(e.step_id == 's1' for e in step_events)

    def test_to_dict_roundtrip(self):
        r = Run(
            run_id='r1', run_type='search', status=RunStatus.DEGRADED,
            started_at='2026-01-01T00:00:00+00:00',
            ended_at='2026-01-01T00:00:01+00:00',
            duration_ms=1000,
            metadata={'query': 'test'},
        )
        r.steps.append(Step(step_id='s1', run_id='r1', name='validate', order=0,
                            status=RunStatus.SUCCEEDED,
                            result=OperationResult.success_with()))
        r.events.append(Event(event_id='e1', run_id='r1', timestamp='t',
                              type=EventType.RUN_STARTED))
        d = r.to_dict()
        assert d['run_id'] == 'r1'
        assert d['run_type'] == 'search'
        assert d['status'] == 'degraded'
        assert d['success'] is True
        assert d['degraded_mode'] is True
        assert len(d['steps']) == 1
        assert len(d['events']) == 1
        assert d['metadata'] == {'query': 'test'}


# ── RunResult tests ─────────────────────────────────────────

class TestRunResult:
    def test_from_run(self):
        r = Run(
            run_id='r1', run_type='search', status=RunStatus.SUCCEEDED,
            started_at='2026-01-01T00:00:00+00:00',
            ended_at='2026-01-01T00:00:01+00:00',
            duration_ms=1000,
        )
        r.steps.append(Step(step_id='s1', run_id='r1', name='validate', order=0,
                            status=RunStatus.SUCCEEDED,
                            result=OperationResult.success_with()))
        r.events.append(Event(event_id='e1', run_id='r1', timestamp='t',
                              type=EventType.RUN_STARTED))

        rr = RunResult.from_run(r)
        assert rr.run_id == 'r1'
        assert rr.run_type == 'search'
        assert rr.status == 'succeeded'
        assert rr.success is True
        assert rr.degraded_mode is False
        assert rr.step_count == 1
        assert rr.event_count == 1
        assert len(rr.steps) == 1
        assert len(rr.events) == 1

    def test_to_dict(self):
        rr = RunResult(
            run_id='r1', run_type='search', status='succeeded',
            success=True, degraded_mode=False,
            started_at='t1', ended_at='t2', duration_ms=500,
            step_count=2, event_count=3,
        )
        d = rr.to_dict()
        assert d['run_id'] == 'r1'
        assert d['status'] == 'succeeded'
        assert d['step_count'] == 2
        assert d['event_count'] == 3
        assert d['error'] is None


# ── RunRecorder tests ───────────────────────────────────────

class TestRunRecorderBasic:
    def test_context_manager_produces_result(self):
        with RunRecorder(run_type='test') as rec:
            pass
        assert isinstance(rec.result, RunResult)
        assert rec.result.run_type == 'test'

    def test_run_id_auto_generated(self):
        with RunRecorder(run_type='test') as rec:
            pass
        assert rec.run_id.startswith('run-')

    def test_run_id_explicit(self):
        with RunRecorder(run_type='test', run_id='my-run-001') as rec:
            pass
        assert rec.run_id == 'my-run-001'

    def test_metadata_preserved(self):
        with RunRecorder(run_type='test', metadata={'q': 'abc'}) as rec:
            pass
        assert rec.result.metadata == {'q': 'abc'}

    def test_empty_run_succeeds(self):
        """A run with no steps is SUCCEEDED (not FAILED)."""
        with RunRecorder(run_type='test') as rec:
            pass
        assert rec.result.status == RunStatus.SUCCEEDED
        assert rec.result.success is True

    def test_started_and_ended_set(self):
        with RunRecorder(run_type='test') as rec:
            pass
        assert rec.result.started_at != ''
        assert rec.result.ended_at != ''
        assert rec.result.duration_ms >= 0

    def test_run_started_event_emitted(self):
        with RunRecorder(run_type='test') as rec:
            pass
        types = [e['type'] for e in rec.result.events]
        assert EventType.RUN_STARTED in types
        assert EventType.RUN_COMPLETED in types


# ── Step recording tests ────────────────────────────────────

class TestStepRecording:
    def test_succeed(self):
        with RunRecorder(run_type='test') as rec:
            with rec.step('validate') as s:
                s.succeed(data={'q': 'normalized'})
        assert rec.result.step_count == 1
        assert rec.result.steps[0]['name'] == 'validate'
        assert rec.result.steps[0]['status'] == 'succeeded'
        assert rec.result.steps[0]['result']['data'] == {'q': 'normalized'}

    def test_degrade(self):
        with RunRecorder(run_type='test') as rec:
            with rec.step('plan') as s:
                s.degrade(reason='no planner')
        assert rec.result.steps[0]['status'] == 'degraded'
        assert rec.result.status == RunStatus.DEGRADED
        assert rec.result.degraded_mode is True

    def test_skip(self):
        with RunRecorder(run_type='test') as rec:
            with rec.step('cache') as s:
                s.skip(reason='no_cache')
        assert rec.result.steps[0]['status'] == 'abandoned'
        assert rec.result.steps[0]['result']['status'] == 'skipped'

    def test_fail(self):
        with RunRecorder(run_type='test') as rec:
            with rec.step('execute') as s:
                s.fail(code='provider_error', message='timeout')
        assert rec.result.steps[0]['status'] == 'failed'
        assert rec.result.status == RunStatus.FAILED
        assert rec.result.success is False

    def test_time_out(self):
        with RunRecorder(run_type='test') as rec:
            with rec.step('fetch') as s:
                s.time_out(message='deadline exceeded')
        assert rec.result.steps[0]['status'] == 'timed_out'
        assert rec.result.status == RunStatus.TIMED_OUT

    def test_auto_complete_succeed(self):
        """Step without explicit terminal call auto-succeeds on clean exit."""
        with RunRecorder(run_type='test') as rec:
            with rec.step('validate'):
                pass  # no explicit terminal
        assert rec.result.steps[0]['status'] == 'succeeded'

    def test_step_order_incremental(self):
        with RunRecorder(run_type='test') as rec:
            with rec.step('a'):
                pass
            with rec.step('b'):
                pass
            with rec.step('c'):
                pass
        orders = [s['order'] for s in rec.result.steps]
        assert orders == [0, 1, 2]

    def test_step_timing_recorded(self):
        with RunRecorder(run_type='test') as rec:
            with rec.step('validate') as s:
                s.succeed()
        step = rec.result.steps[0]
        assert step['started_at'] is not None
        assert step['ended_at'] is not None
        assert step['duration_ms'] >= 0


# ── Status derivation tests ─────────────────────────────────

class TestStatusDerivation:
    def test_all_succeed_yields_succeeded(self):
        with RunRecorder(run_type='test') as rec:
            with rec.step('a'):
                s.succeed() if (s := None) else None  # placeholder
            # Redo properly
        with RunRecorder(run_type='test') as rec:
            with rec.step('a') as st:
                st.succeed()
            with rec.step('b') as st:
                st.succeed()
        assert rec.result.status == RunStatus.SUCCEEDED

    def test_any_degraded_yields_degraded(self):
        with RunRecorder(run_type='test') as rec:
            with rec.step('a') as st:
                st.succeed()
            with rec.step('b') as st:
                st.degrade(reason='fallback')
        assert rec.result.status == RunStatus.DEGRADED

    def test_any_failed_yields_failed(self):
        with RunRecorder(run_type='test') as rec:
            with rec.step('a') as st:
                st.succeed()
            with rec.step('b') as st:
                st.fail(message='boom')
        assert rec.result.status == RunStatus.FAILED

    def test_failed_takes_precedence_over_degraded(self):
        with RunRecorder(run_type='test') as rec:
            with rec.step('a') as st:
                st.degrade(reason='fallback')
            with rec.step('b') as st:
                st.fail(message='boom')
        assert rec.result.status == RunStatus.FAILED

    def test_timed_out_takes_precedence_over_degraded(self):
        with RunRecorder(run_type='test') as rec:
            with rec.step('a') as st:
                st.degrade(reason='slow')
            with rec.step('b') as st:
                st.time_out()
        assert rec.result.status == RunStatus.TIMED_OUT

    def test_explicit_succeed_overrides_derived(self):
        with RunRecorder(run_type='test') as rec:
            with rec.step('a') as st:
                st.fail(message='non-fatal')
            rec.succeed()
        assert rec.result.status == RunStatus.SUCCEEDED


# ── Exception handling tests ────────────────────────────────

class TestExceptionHandling:
    def test_exception_in_step_fails_step(self):
        with pytest.raises(ValueError):
            with RunRecorder(run_type='test') as rec:
                with rec.step('boom') as st:
                    raise ValueError('kaboom')
        assert rec.result.steps[0]['status'] == 'failed'
        assert rec.result.steps[0]['result']['errors'][0]['code'] == 'exception'

    def test_exception_fails_run(self):
        with pytest.raises(ValueError):
            with RunRecorder(run_type='test') as rec:
                with rec.step('boom'):
                    raise ValueError('kaboom')
        assert rec.result.status == RunStatus.FAILED
        assert 'kaboom' in (rec.result.error or '')

    def test_exception_not_suppressed(self):
        """The recorder must NOT swallow exceptions — they propagate to caller."""
        with pytest.raises(RuntimeError, match='propagated'):
            with RunRecorder(run_type='test'):
                raise RuntimeError('propagated')

    def test_listener_error_is_non_fatal(self):
        """A listener that raises must not crash the recorder."""
        def bad_listener(evt):
            raise RuntimeError('listener broken')

        with RunRecorder(run_type='test', listeners=[bad_listener]) as rec:
            rec.event(EventType.CUSTOM, payload={'test': True})
        # Run completes despite listener error
        assert rec.result.status == RunStatus.SUCCEEDED


# ── Event emission tests ────────────────────────────────────

class TestEventEmission:
    def test_run_level_event(self):
        with RunRecorder(run_type='test') as rec:
            rec.event(EventType.CACHE_HIT, payload={'key': 'abc'})
        events = [e for e in rec.result.events if e['type'] == 'cache.hit']
        assert len(events) == 1
        assert events[0]['step_id'] is None
        assert events[0]['payload'] == {'key': 'abc'}

    def test_step_level_event(self):
        with RunRecorder(run_type='test') as rec:
            with rec.step('validate') as s:
                s.event(EventType.CACHE_MISS, payload={'reason': 'empty'})
        events = [e for e in rec.result.events if e['type'] == 'cache.miss']
        assert len(events) == 1
        assert events[0]['step_id'] is not None
        assert events[0]['step_name'] == 'validate'

    def test_events_are_append_only(self):
        """Events list grows; existing events are never mutated."""
        with RunRecorder(run_type='test') as rec:
            rec.event(EventType.CUSTOM, payload={'i': 1})
            initial_count = len(rec.run.events)
            rec.event(EventType.CUSTOM, payload={'i': 2})
            assert len(rec.run.events) == initial_count + 1

    def test_step_emits_started_and_completed(self):
        with RunRecorder(run_type='test') as rec:
            with rec.step('validate') as s:
                s.succeed()
        step_events = [e for e in rec.result.events
                       if e['step_name'] == 'validate']
        types = [e['type'] for e in step_events]
        assert EventType.STEP_STARTED in types
        assert EventType.STEP_COMPLETED in types

    def test_failed_step_emits_step_failed(self):
        with RunRecorder(run_type='test') as rec:
            with rec.step('execute') as s:
                s.fail(message='boom')
        step_events = [e for e in rec.result.events
                       if e['step_name'] == 'execute']
        types = [e['type'] for e in step_events]
        assert EventType.STEP_FAILED in types

    def test_event_level_warn(self):
        with RunRecorder(run_type='test') as rec:
            rec.event(EventType.WARNING, level=EventLevel.WARN,
                      payload={'msg': 'slow'})
        warn_events = [e for e in rec.result.events if e['level'] == 'warn']
        assert len(warn_events) == 1


# ── Listener tests ──────────────────────────────────────────

class TestListeners:
    def test_listener_receives_events(self):
        received = []
        with RunRecorder(run_type='test', listeners=[received.append]) as rec:
            rec.event(EventType.CUSTOM, payload={'i': 1})
            with rec.step('s') as s:
                s.succeed()
        # RUN_STARTED, CUSTOM, STEP_STARTED, STEP_COMPLETED, RUN_COMPLETED
        assert len(received) >= 5
        assert all(isinstance(e, Event) for e in received)

    def test_multiple_listeners(self):
        a, b = [], []
        with RunRecorder(run_type='test', listeners=[a.append, b.append]) as rec:
            rec.event(EventType.CUSTOM)
        assert len(a) == len(b)
        assert len(a) >= 2  # RUN_STARTED + CUSTOM + RUN_COMPLETED


# ── Adapter tests ───────────────────────────────────────────

class TestRunFromStepReports:
    def test_builds_run_with_steps(self):
        reports = {
            'validate': OperationResult.success_with(step='validate'),
            'plan': OperationResult.degraded(step='plan', reason='no planner'),
            'execute': OperationResult.success_with(step='execute'),
        }
        run = run_from_step_reports('search', reports)
        assert run.run_type == 'search'
        assert len(run.steps) == 3
        assert run.steps[0].name == 'validate'
        assert run.steps[1].name == 'plan'
        assert run.steps[2].name == 'execute'

    def test_derives_degraded_from_step(self):
        reports = {
            'validate': OperationResult.success_with(),
            'plan': OperationResult.degraded(reason='fallback'),
        }
        run = run_from_step_reports('search', reports)
        assert run.status == RunStatus.DEGRADED

    def test_derives_failed_from_step(self):
        reports = {
            'validate': OperationResult.failed(code='boom'),
            'plan': OperationResult.success_with(),
        }
        run = run_from_step_reports('search', reports)
        assert run.status == RunStatus.FAILED

    def test_degraded_mode_flag(self):
        reports = {'validate': OperationResult.success_with()}
        run = run_from_step_reports('search', reports, degraded_mode=True)
        assert run.status == RunStatus.DEGRADED
        assert run.degraded_mode is True

    def test_empty_reports_yields_pending(self):
        run = run_from_step_reports('search', {})
        assert run.status == RunStatus.PENDING
        assert len(run.steps) == 0

    def test_preserves_metadata_and_error(self):
        reports = {'validate': OperationResult.success_with()}
        run = run_from_step_reports('search', reports,
                                    metadata={'query': 'test'},
                                    error='partial failure')
        assert run.metadata == {'query': 'test'}
        assert run.error == 'partial failure'

    def test_step_status_from_operation_result(self):
        """Step.status is derived from OperationResult.status."""
        reports = {
            'skipped_step': OperationResult.skipped(reason='disabled'),
        }
        run = run_from_step_reports('test', reports)
        assert run.steps[0].status == 'skipped'


# ── add_step_result (adapter on recorder) tests ─────────────

class TestAddStepResult:
    def test_adds_precomputed_step(self):
        with RunRecorder(run_type='test') as rec:
            rec.add_step_result('validate',
                                OperationResult.success_with(data={'q': 'x'}))
        assert rec.result.step_count == 1
        assert rec.result.steps[0]['name'] == 'validate'
        assert rec.result.steps[0]['result']['data'] == {'q': 'x'}

    def test_add_step_result_derives_run_status(self):
        with RunRecorder(run_type='test') as rec:
            rec.add_step_result('a', OperationResult.success_with())
            rec.add_step_result('b', OperationResult.degraded(reason='fallback'))
        assert rec.result.status == RunStatus.DEGRADED


# ── Integration: realistic search pipeline run ──────────────

class TestIntegrationSearchRun:
    def test_full_search_run_with_events(self):
        """Simulates a realistic search pipeline run with cache hit and degradation."""
        with RunRecorder(run_type='search', run_id='search-001',
                         metadata={'query': 'python asyncio'}) as rec:
            with rec.step('validate_request') as s:
                s.succeed(data={'query': 'python asyncio'})

            with rec.step('normalize_query') as s:
                s.succeed(data={'normalized': 'python asyncio'})

            with rec.step('final_cache_lookup') as s:
                s.event(EventType.CACHE_MISS, payload={'key': 'hash123'})
                s.skip(reason='cache miss')

            with rec.step('plan') as s:
                s.degrade(reason='no planner injected',
                          data={'sub_queries': 1})

            with rec.step('provider_execute') as s:
                s.event(EventType.PROVIDER_CALLED,
                        payload={'provider': 'serper'})
                s.succeed(data={'results': 3})

            with rec.step('cache_store') as s:
                s.event(EventType.CACHE_STORE, payload={'key': 'hash123'})
                s.succeed()

        r = rec.result
        assert r.run_type == 'search'
        assert r.run_id == 'search-001'
        assert r.status == RunStatus.DEGRADED
        assert r.success is True
        assert r.degraded_mode is True
        assert r.step_count == 6

        # Verify events include both run-level and step-level
        event_types = [e['type'] for e in r.events]
        assert EventType.RUN_STARTED in event_types
        assert EventType.RUN_COMPLETED in event_types
        assert EventType.CACHE_MISS in event_types
        assert EventType.PROVIDER_CALLED in event_types
        assert EventType.CACHE_STORE in event_types

        # Verify step ordering
        step_names = [s['name'] for s in r.steps]
        assert step_names == [
            'validate_request', 'normalize_query', 'final_cache_lookup',
            'plan', 'provider_execute', 'cache_store',
        ]
