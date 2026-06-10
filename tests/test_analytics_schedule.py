"""S3 Analytics — DigestSchedule: каденс/время/вкл, созревание (фейк-часы).

Покрывает:
  A6 — DigestSchedule настраивается (каденс/время/вкл); выключение останавливает.
  A2 (часть) — «созрело ли» расписание относительно своего last_sent_at + времени.

Часы — фейковые (передаём `now`, наивный UTC), без живого времени. Таймзона владельца
семьи (ТЗ §3.1, F6.4): is_due считает «наступило ли time_of_day сегодня» в локальной
зоне. Чтобы изолировать логику созревания от DST, ядро тестов гоняем с tz="UTC"
(тогда наивный-UTC now == локальные стенные часы). Преобразование зоны/DST само по себе
проверяется в test_analytics_digest (job с реальной зоной Amsterdam).
"""

from datetime import datetime, time

from app.analytics.schedule import (
    Cadence,
    DigestSchedule,
    default_schedules,
    is_due,
    period_bounds,
)

TZ = "UTC"


# ── A6: дефолт-сид (Катя): daily 23:59 + weekly вс 23:00 ──────────────────────

def test_default_seed_two_schedules():
    seeds = default_schedules(family_id=7)
    assert len(seeds) == 2
    by_cad = {s.cadence: s for s in seeds}
    daily = by_cad[Cadence.DAILY]
    weekly = by_cad[Cadence.WEEKLY]
    assert daily.time_of_day == time(23, 59)
    assert daily.weekday is None
    assert weekly.time_of_day == time(23, 0)
    assert weekly.weekday == 6  # вс=6 (ТЗ §3.1)
    assert all(s.enabled for s in seeds)
    assert all(s.family_id == 7 for s in seeds)
    assert all(s.last_sent_at is None for s in seeds)


# ── A6 / A2: созревание daily ─────────────────────────────────────────────────

def test_daily_due_after_time_when_never_sent():
    s = DigestSchedule(
        family_id=1, cadence=Cadence.DAILY, time_of_day=time(23, 59),
        enabled=True, last_sent_at=None,
    )
    # 2026-06-10 23:59 локального — наступило, ещё не слали → созрело.
    now = datetime(2026, 6, 10, 23, 59)
    assert is_due(s, now, tz_name=TZ) is True
    # 2026-06-10 12:00 — время ещё не наступило сегодня → не созрело.
    assert is_due(s, datetime(2026, 6, 10, 12, 0), tz_name=TZ) is False


def test_daily_not_due_same_day_already_sent():
    """Повторный тик в тот же день не дублирует (last_sent_at сегодня)."""
    s = DigestSchedule(
        family_id=1, cadence=Cadence.DAILY, time_of_day=time(23, 59),
        enabled=True, last_sent_at=datetime(2026, 6, 10, 23, 59),
    )
    # Тот же день, позже — уже слали сегодня → не созрело.
    assert is_due(s, datetime(2026, 6, 10, 23, 59, 30), tz_name=TZ) is False
    # Следующий день после времени — снова созрело.
    assert is_due(s, datetime(2026, 6, 11, 23, 59), tz_name=TZ) is True


def test_disabled_never_due():
    """A6: enabled=false → расписание не созревает (рассылка остановлена)."""
    s = DigestSchedule(
        family_id=1, cadence=Cadence.DAILY, time_of_day=time(23, 59),
        enabled=False, last_sent_at=None,
    )
    assert is_due(s, datetime(2026, 6, 10, 23, 59), tz_name=TZ) is False


# ── A2: созревание weekly (по weekday) ────────────────────────────────────────

def test_weekly_due_only_on_weekday():
    s = DigestSchedule(
        family_id=1, cadence=Cadence.WEEKLY, weekday=6, time_of_day=time(23, 0),
        enabled=True, last_sent_at=None,
    )
    # 2026-06-14 — воскресенье. 23:00 наступило → созрело.
    sunday = datetime(2026, 6, 14, 23, 0)
    assert sunday.weekday() == 6
    assert is_due(s, sunday, tz_name=TZ) is True
    # 2026-06-13 — суббота, не тот weekday → не созрело даже после времени.
    saturday = datetime(2026, 6, 13, 23, 30)
    assert saturday.weekday() == 5
    assert is_due(s, saturday, tz_name=TZ) is False


def test_weekly_not_due_twice_same_week():
    s = DigestSchedule(
        family_id=1, cadence=Cadence.WEEKLY, weekday=6, time_of_day=time(23, 0),
        enabled=True, last_sent_at=datetime(2026, 6, 14, 23, 0),
    )
    # То же воскресенье позже → уже слали → не созрело.
    assert is_due(s, datetime(2026, 6, 14, 23, 5), tz_name=TZ) is False
    # Следующее воскресенье → созрело.
    assert is_due(s, datetime(2026, 6, 21, 23, 0), tz_name=TZ) is True


# ── A2/I6: период «с last_sent_at» ────────────────────────────────────────────

def test_period_bounds_uses_last_sent_at():
    """Охват = (last_sent_at, now]; в датах локальной зоны."""
    s = DigestSchedule(
        family_id=1, cadence=Cadence.DAILY, time_of_day=time(23, 59),
        enabled=True, last_sent_at=datetime(2026, 6, 9, 23, 59),
    )
    now = datetime(2026, 6, 10, 23, 59)
    date_from, date_to = period_bounds(s, now, tz_name=TZ)
    assert date_to.isoformat() == "2026-06-10"
    # Старт — день last_sent_at (включительно) или ранее.
    assert date_from.isoformat() <= "2026-06-09"


def test_period_bounds_first_run_falls_back_to_cadence_window():
    """Первый запуск (last_sent_at=None) — окно по каденсу: daily≈день, weekly≈неделя."""
    daily = DigestSchedule(
        family_id=1, cadence=Cadence.DAILY, time_of_day=time(23, 59),
        enabled=True, last_sent_at=None,
    )
    now = datetime(2026, 6, 10, 23, 59)
    df, dt = period_bounds(daily, now, tz_name=TZ)
    assert dt.isoformat() == "2026-06-10"
    assert (dt - df).days <= 1

    weekly = DigestSchedule(
        family_id=1, cadence=Cadence.WEEKLY, weekday=6, time_of_day=time(23, 0),
        enabled=True, last_sent_at=None,
    )
    df_w, dt_w = period_bounds(weekly, now, tz_name=TZ)
    assert (dt_w - df_w).days >= 6
