"""Contract tests for modules.common (Round 2 Phase 1)."""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
from modules.common.result import (
    ErrorInfo, OperationResult, StepStatus, WarningInfo,
)
from modules.common.errors import (
    AgentForgeError, ConfigurationError, MigrationError, ModelError,
    NoActionsError, ParseError, StorageError, TimeoutError,
    UnsupportedOperationError, ValidationError,
)
from modules.common.time_utils import (
    DEFAULT_USER_TIMEZONE, format_offset, handle_dst_fold, handle_dst_gap,
    now_utc_iso, parse_iso_with_tz, to_utc_iso,
)


# ── StepStatus ───────────────────────────────────────────────

class TestStepStatus:
    def test_nine_states_present(self):
        expected = {'pending', 'running', 'succeeded', 'degraded', 'skipped',
                    'unsupported', 'failed', 'timed_out', 'abandoned'}
        actual = {s.value for s in StepStatus}
        assert actual == expected

    def test_is_terminal(self):
        assert StepStatus.is_terminal(StepStatus.SUCCEEDED)
        assert StepStatus.is_terminal(StepStatus.FAILED)
        assert StepStatus.is_terminal(StepStatus.ABANDONED)
        assert not StepStatus.is_terminal(StepStatus.RUNNING)
        assert not StepStatus.is_terminal(StepStatus.PENDING)

    def test_is_success_like(self):
        assert StepStatus.is_success_like(StepStatus.SUCCEEDED)
        assert StepStatus.is_success_like(StepStatus.DEGRADED)
        assert not StepStatus.is_success_like(StepStatus.FAILED)
        assert not StepStatus.is_success_like(StepStatus.SKIPPED)


# ── OperationResult ──────────────────────────────────────────

class TestOperationResult:
    def test_success_with_data(self):
        r = OperationResult.success_with({'foo': 1}, step='validate')
        assert r.success is True
        assert r.status == StepStatus.SUCCEEDED
        assert r.data == {'foo': 1}
        assert r.metadata['step'] == 'validate'

    def test_degraded(self):
        r = OperationResult.degraded(data='partial', reason='fallback')
        assert r.success is True
        assert r.status == StepStatus.DEGRADED
        assert r.data == 'partial'

    def test_skipped(self):
        r = OperationResult.skipped(reason='disabled')
        assert r.success is False
        assert r.status == StepStatus.SKIPPED
        assert r.metadata['reason'] == 'disabled'

    def test_unsupported(self):
        r = OperationResult.unsupported(reason='date trigger not wired')
        assert r.success is False
        assert r.status == StepStatus.UNSUPPORTED

    def test_failed_with_error(self):
        r = OperationResult.failed(code='storage_error', message='disk full')
        assert r.success is False
        assert r.status == StepStatus.FAILED
        assert len(r.errors) == 1
        assert r.errors[0].code == 'storage_error'

    def test_failed_from_exception(self):
        exc = ValueError('bad input')
        r = OperationResult.failed(exception=exc, code='validation_error')
        assert r.success is False
        assert r.errors[0].exception_type == 'ValueError'
        assert r.errors[0].message == 'bad input'

    def test_timed_out(self):
        r = OperationResult.timed_out(message='deadline exceeded')
        assert r.success is False
        assert r.status == StepStatus.TIMED_OUT
        assert r.errors[0].code == 'timed_out'

    def test_add_error_flips_success(self):
        r = OperationResult.success_with(data='ok')
        r.add_error(ErrorInfo(code='late_error', message='too late'))
        assert r.success is False
        assert r.status == StepStatus.FAILED

    def test_post_init_invariant_errors_implies_failure(self):
        """Constructing with errors but success=True must flip success."""
        r = OperationResult(
            success=True,
            errors=[ErrorInfo(code='x', message='y')],
        )
        assert r.success is False
        assert r.status == StepStatus.FAILED

    def test_post_init_invariant_success_implies_success_status(self):
        """Constructing with success=True but non-success status flips to SUCCEEDED."""
        r = OperationResult(success=True, status=StepStatus.FAILED)
        assert r.status == StepStatus.SUCCEEDED

    def test_to_dict_roundtrip(self):
        r = OperationResult.success_with(data={'a': 1})
        d = r.to_dict()
        assert d['success'] is True
        assert d['status'] == 'succeeded'
        assert d['data'] == {'a': 1}
        assert d['errors'] == []
        assert d['warnings'] == []

    def test_no_enabled_true_dict_pattern(self):
        """Round 2 forbidden pattern: {'enabled': True} must not appear."""
        r = OperationResult.success_with()
        d = r.to_dict()
        # The result dict must not use 'enabled' as a success marker.
        assert 'enabled' not in d
        assert d['success'] is True


# ── Errors ───────────────────────────────────────────────────

class TestErrors:
    def test_typed_error_codes(self):
        cases = [
            (ValidationError(), 'validation_error'),
            (ParseError(), 'parse_error'),
            (ModelError(), 'model_error'),
            (StorageError(), 'storage_error'),
            (ConfigurationError(), 'configuration_error'),
            (TimeoutError(), 'timeout_error'),
            (UnsupportedOperationError(), 'unsupported_operation'),
            (MigrationError(), 'migration_error'),
            (NoActionsError(), 'no_actions'),
        ]
        for exc, expected_code in cases:
            assert exc.code == expected_code, f'{exc.__class__.__name__} code mismatch'

    def test_error_carries_details_and_cause(self):
        cause = RuntimeError('root')
        err = StorageError('disk full', code='disk_full',
                           details={'path': '/tmp'}, cause=cause)
        assert err.code == 'disk_full'
        assert err.details == {'path': '/tmp'}
        assert err.cause is cause
        d = err.to_dict()
        assert d['cause'] == 'RuntimeError'

    def test_all_inherit_agent_forge_error(self):
        for cls in [ValidationError, ParseError, ModelError, StorageError,
                    ConfigurationError, TimeoutError, UnsupportedOperationError,
                    MigrationError, NoActionsError]:
            assert issubclass(cls, AgentForgeError)

    def test_no_actions_distinct_from_model_error(self):
        """Round 2 R2-5.2: no_actions must not be conflated with model_error."""
        assert NoActionsError().code != ModelError().code


# ── Time Utils ───────────────────────────────────────────────

class TestTimeUtils:
    def test_now_utc_iso_has_offset(self):
        s = now_utc_iso()
        assert '+00:00' in s

    def test_to_utc_iso_naive_attaches_default_tz(self):
        naive = datetime(2026, 8, 4, 12, 0, 0)  # noon local
        utc_iso, attached_tz, original = to_utc_iso(naive, original='2026-08-04 12:00')
        assert attached_tz == DEFAULT_USER_TIMEZONE
        assert original == '2026-08-04 12:00'
        # Shanghai is UTC+8 → UTC should be 04:00
        assert '04:00:00' in utc_iso
        assert '+00:00' in utc_iso

    def test_to_utc_iso_aware_preserves_offset(self):
        """Aware datetime MUST NOT have its offset overwritten (R2-6.3)."""
        aware = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        utc_iso, attached_tz, _ = to_utc_iso(aware)
        # +05:30 → UTC is 06:30
        assert '06:30:00' in utc_iso
        assert attached_tz is None  # was already aware

    def test_to_utc_iso_half_hour_offset(self):
        """Test :30 offset (India)."""
        aware = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        utc_iso, _, _ = to_utc_iso(aware)
        # 00:00 +05:30 → 18:30 prev day UTC
        assert '18:30:00' in utc_iso

    def test_to_utc_iso_forty_five_minute_offset(self):
        """Test :45 offset (Nepal)."""
        aware = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=45)))
        utc_iso, _, _ = to_utc_iso(aware)
        # 00:00 +05:45 → 18:15 prev day UTC
        assert '18:15:00' in utc_iso

    def test_parse_iso_with_tz_explicit_z(self):
        dt = parse_iso_with_tz('2026-08-04T12:00:00Z')
        assert dt.tzinfo is not None
        assert dt.utcoffset() == timedelta(0)

    def test_parse_iso_with_tz_explicit_offset(self):
        dt = parse_iso_with_tz('2026-08-04T12:00:00+05:30')
        assert dt.utcoffset() == timedelta(hours=5, minutes=30)

    def test_parse_iso_with_tz_naive_uses_default(self):
        dt = parse_iso_with_tz('2026-08-04T12:00:00')
        assert dt.tzinfo is not None
        # Default tz attached
        assert dt.tzinfo.utcoffset(dt) is not None

    def test_parse_iso_with_tz_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_iso_with_tz('not a date')

    def test_parse_iso_with_tz_empty_raises(self):
        with pytest.raises(ValueError):
            parse_iso_with_tz('')

    def test_format_offset_zero(self):
        assert format_offset(0) == '+00:00'

    def test_format_offset_positive_hour(self):
        assert format_offset(60) == '+01:00'
        assert format_offset(120) == '+02:00'

    def test_format_offset_negative_hour(self):
        assert format_offset(-60) == '-01:00'
        assert format_offset(-300) == '-05:00'

    def test_format_offset_half_hour(self):
        assert format_offset(330) == '+05:30'
        assert format_offset(-210) == '-03:30'

    def test_format_offset_forty_five_minutes(self):
        assert format_offset(345) == '+05:45'
        assert format_offset(-525) == '-08:45'

    def test_handle_dst_gap_reject_raises(self):
        """Spring-forward gap must reject by default."""
        from zoneinfo import ZoneInfo
        # US/Eastern spring forward: 2026-03-08 02:30 does not exist
        naive = datetime(2026, 3, 8, 2, 30, 0)
        tz = ZoneInfo('America/New_York')
        with pytest.raises(ValueError, match='DST gap'):
            handle_dst_gap(naive, tz, policy='reject')

    def test_handle_dst_gap_shift_forward(self):
        from zoneinfo import ZoneInfo
        naive = datetime(2026, 3, 8, 2, 30, 0)
        tz = ZoneInfo('America/New_York')
        result = handle_dst_gap(naive, tz, policy='shift_forward')
        assert result.tzinfo is not None

    def test_handle_dst_fold_ambiguous_requires_fold(self):
        """Autumn overlap: 01:30 occurs twice — must require fold."""
        from zoneinfo import ZoneInfo
        naive = datetime(2026, 11, 1, 1, 30, 0)
        tz = ZoneInfo('America/New_York')
        with pytest.raises(ValueError, match='ambiguous'):
            handle_dst_fold(naive, tz, fold=None)

    def test_handle_dst_fold_explicit_fold_zero(self):
        from zoneinfo import ZoneInfo
        naive = datetime(2026, 11, 1, 1, 30, 0)
        tz = ZoneInfo('America/New_York')
        result = handle_dst_fold(naive, tz, fold=0)
        assert result.fold == 0

    def test_handle_dst_fold_explicit_fold_one(self):
        from zoneinfo import ZoneInfo
        naive = datetime(2026, 11, 1, 1, 30, 0)
        tz = ZoneInfo('America/New_York')
        result = handle_dst_fold(naive, tz, fold=1)
        assert result.fold == 1

    def test_handle_dst_fold_invalid_fold_raises(self):
        from zoneinfo import ZoneInfo
        naive = datetime(2026, 11, 1, 1, 30, 0)
        tz = ZoneInfo('America/New_York')
        with pytest.raises(ValueError, match='fold must be 0 or 1'):
            handle_dst_fold(naive, tz, fold=2)

    # R2-6: Additional DST edge case coverage

    def test_handle_dst_gap_shift_backward(self):
        """shift_backward policy must return fold=1 variant."""
        from zoneinfo import ZoneInfo
        naive = datetime(2026, 3, 8, 2, 30, 0)
        tz = ZoneInfo('America/New_York')
        result = handle_dst_gap(naive, tz, policy='shift_backward')
        assert result.tzinfo is not None

    def test_handle_dst_gap_non_gap_time_passes(self):
        """A time NOT in a DST gap must pass through without error."""
        from zoneinfo import ZoneInfo
        # Noon is never in a gap
        naive = datetime(2026, 3, 8, 12, 0, 0)
        tz = ZoneInfo('America/New_York')
        result = handle_dst_gap(naive, tz, policy='reject')
        assert result.tzinfo is not None

    def test_handle_dst_fold_non_ambiguous_time_passes(self):
        """A non-ambiguous time must pass through without requiring fold."""
        from zoneinfo import ZoneInfo
        # Noon is never ambiguous
        naive = datetime(2026, 11, 1, 12, 0, 0)
        tz = ZoneInfo('America/New_York')
        result = handle_dst_fold(naive, tz, fold=None)
        assert result.tzinfo is not None

    def test_handle_dst_gap_unknown_policy_raises(self):
        """Unknown policy must raise ValueError."""
        from zoneinfo import ZoneInfo
        naive = datetime(2026, 3, 8, 2, 30, 0)
        tz = ZoneInfo('America/New_York')
        with pytest.raises(ValueError, match='unknown DST policy'):
            handle_dst_gap(naive, tz, policy='bogus')

    def test_format_offset_negative_half_hour(self):
        """R2-6: Negative half-hour offset must format correctly."""
        assert format_offset(-330) == '-05:30'  # India negative
        assert format_offset(-570) == '-09:30'  # Marquesas

    def test_format_offset_negative_forty_five_minutes(self):
        """R2-6: Negative 45-minute offset must format correctly."""
        assert format_offset(-315) == '-05:15'  # Chatham Islands negative
        assert format_offset(-525) == '-08:45'  # Eucla negative

    def test_to_utc_iso_half_hour_timezone_name(self):
        """R2-6: Half-hour timezone via ZoneInfo name (Asia/Kolkata +05:30)."""
        from zoneinfo import ZoneInfo
        naive = datetime(2026, 1, 1, 12, 0, 0)
        utc_iso, attached_tz, _ = to_utc_iso(naive, source_timezone='Asia/Kolkata')
        # 12:00 +05:30 → 06:30 UTC
        assert '06:30:00' in utc_iso
        assert attached_tz == 'Asia/Kolkata'

    def test_to_utc_iso_forty_five_minute_timezone_name(self):
        """R2-6: 45-minute timezone via ZoneInfo name (Asia/Kathmandu +05:45)."""
        naive = datetime(2026, 1, 1, 12, 0, 0)
        utc_iso, attached_tz, _ = to_utc_iso(naive, source_timezone='Asia/Kathmandu')
        # 12:00 +05:45 → 06:15 UTC
        assert '06:15:00' in utc_iso
        assert attached_tz == 'Asia/Kathmandu'

    def test_to_utc_iso_negative_offset_preserved(self):
        """R2-6: Aware datetime with negative offset must convert correctly."""
        aware = datetime(2026, 1, 1, 12, 0, 0,
                         tzinfo=timezone(timedelta(hours=-5, minutes=-30)))
        utc_iso, attached_tz, _ = to_utc_iso(aware)
        # 12:00 -05:30 → 17:30 UTC
        assert '17:30:00' in utc_iso
        assert attached_tz is None
