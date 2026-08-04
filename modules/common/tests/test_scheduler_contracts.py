"""Contract tests for modules.scheduler.contracts (Round 2 Phase 1)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
from modules.scheduler.contracts import (
    ActionItem, ActionItemStatus, JobRun, JobRunStatus, MigrationOutcome,
    QuarantinedJob, ScheduledJob, TriggerSpec, TriggerType,
)


# ── TriggerType ──────────────────────────────────────────────

class TestTriggerType:
    def test_only_cron_supported(self):
        """R2-4.2: supported_trigger_types = ['cron']."""
        supported = TriggerType.supported()
        assert supported == [TriggerType.CRON]
        assert TriggerType.DATE not in supported
        assert TriggerType.INTERVAL not in supported


# ── TriggerSpec ──────────────────────────────────────────────

class TestTriggerSpec:
    def test_cron_trigger_requires_expression(self):
        with pytest.raises(ValueError, match='cron_expression'):
            TriggerSpec(type=TriggerType.CRON)

    def test_cron_trigger_to_apscheduler_kwargs(self):
        """R2-4.3: timezone, max_instances, misfire_grace_time, coalesce passed."""
        spec = TriggerSpec(
            type=TriggerType.CRON,
            cron_expression='0 9 * * 1-5',
            timezone='Asia/Shanghai',
            max_instances=2,
            misfire_grace_time=120,
            coalesce=False,
        )
        kwargs = spec.to_apscheduler_kwargs()
        assert kwargs['trigger'] == 'cron'
        assert kwargs['minute'] == '0'
        assert kwargs['hour'] == '9'
        assert kwargs['day'] == '*'
        assert kwargs['month'] == '*'
        assert kwargs['day_of_week'] == '1-5'
        assert kwargs['timezone'] == 'Asia/Shanghai'
        assert kwargs['max_instances'] == 2
        assert kwargs['misfire_grace_time'] == 120
        assert kwargs['coalesce'] is False

    def test_date_trigger_not_wired(self):
        """R2-4.2: date trigger raises NotImplementedError when building kwargs."""
        spec = TriggerSpec(type=TriggerType.DATE, run_date=None)
        # Construction allowed (forward compat); to_apscheduler_kwargs raises.
        with pytest.raises(NotImplementedError, match='not wired'):
            spec.to_apscheduler_kwargs()

    def test_interval_trigger_not_wired(self):
        spec = TriggerSpec(type=TriggerType.INTERVAL, interval_seconds=60)
        # cron_expression not required for non-cron; but post_init only checks cron
        with pytest.raises(NotImplementedError, match='not wired'):
            spec.to_apscheduler_kwargs()

    def test_cron_expression_must_be_five_fields(self):
        spec = TriggerSpec(type=TriggerType.CRON, cron_expression='0 9 * *')
        with pytest.raises(ValueError, match='5 fields'):
            spec.to_apscheduler_kwargs()


# ── ActionItem / ScheduledJob / JobRun separation ────────────

class TestDomainSeparation:
    """R2-4.1: action_items, scheduled_jobs, job_runs are distinct tables."""

    def test_action_item_can_exist_without_job(self):
        """ActionItem is independent of ScheduledJob."""
        ai = ActionItem(id='ai-1', title='Buy milk')
        assert ai.status == ActionItemStatus.PENDING
        assert ai.due_at_utc is None

    def test_scheduled_job_references_action_item(self):
        """ScheduledJob references ActionItem via action_item_id."""
        spec = TriggerSpec(cron_expression='0 9 * * *')
        job = ScheduledJob(id='job-1', action_item_id='ai-1', trigger=spec)
        assert job.action_item_id == 'ai-1'
        assert job.run_count == 0
        assert job.enabled is True

    def test_job_run_references_scheduled_job(self):
        """JobRun references ScheduledJob via job_id."""
        run = JobRun(id='run-1', job_id='job-1')
        assert run.job_id == 'job-1'
        assert run.status == JobRunStatus.QUEUED
        assert run.triggered_by == 'cron'

    def test_three_distinct_types(self):
        """The three classes are distinct; no conflation."""
        ai = ActionItem(id='ai-1', title='x')
        spec = TriggerSpec(cron_expression='0 9 * * *')
        job = ScheduledJob(id='job-1', action_item_id='ai-1', trigger=spec)
        run = JobRun(id='run-1', job_id='job-1')
        assert type(ai).__name__ == 'ActionItem'
        assert type(job).__name__ == 'ScheduledJob'
        assert type(run).__name__ == 'JobRun'

    def test_action_item_carries_original_due_at(self):
        """R2-5.1: original_due_at preserved verbatim alongside UTC."""
        ai = ActionItem(
            id='ai-1', title='Test',
            due_at_utc='2026-08-04T01:00:00+00:00',
            original_due_at='2026-08-04 09:00',
            source_timezone='Asia/Shanghai',
        )
        d = ai.to_dict()
        assert d['due_at_utc'] == '2026-08-04T01:00:00+00:00'
        assert d['original_due_at'] == '2026-08-04 09:00'
        assert d['source_timezone'] == 'Asia/Shanghai'


# ── JobRun status flow ───────────────────────────────────────

class TestJobRunStatus:
    def test_queued_is_initial_status(self):
        """R2-4.5: manual trigger returns status=QUEUED."""
        run = JobRun(id='r', job_id='j')
        assert run.status == JobRunStatus.QUEUED

    def test_all_terminal_states_present(self):
        expected = {'queued', 'running', 'succeeded', 'failed',
                    'timed_out', 'abandoned'}
        actual = {s.value for s in JobRunStatus}
        assert actual == expected


# ── Quarantine ───────────────────────────────────────────────

class TestQuarantine:
    def test_quarantine_records_reason(self):
        """R2-4.6: unmapped jobs enter quarantine, not auto-converted."""
        q = QuarantinedJob(
            id='q-1',
            raw_legacy={'type': 'weird', 'data': '...'},
            reason='unknown trigger type: solar',
        )
        d = q.to_dict()
        assert d['reason'] == 'unknown trigger type: solar'
        assert d['raw_legacy'] == {'type': 'weird', 'data': '...'}


# ── Migration outcome ────────────────────────────────────────

class TestMigrationOutcome:
    def test_three_outcomes(self):
        """R2-4.6: migrated / quarantined / skipped (idempotent)."""
        outcomes = {o.value for o in MigrationOutcome}
        assert outcomes == {'migrated', 'quarantined', 'skipped'}
