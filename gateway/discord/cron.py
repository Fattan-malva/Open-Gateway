"""Cron minimal 5-field: menit jam tanggal bulan hari.

Cukup untuk kebutuhan bot; tanpa dependency tambahan.
"""
import time
from datetime import datetime
from typing import Iterable, Set


class CronError(ValueError):
    pass


def _values(expr: str, lo: int, hi: int) -> Set[int]:
    out: Set[int] = set()
    for part in (expr or "").split(","):
        part = part.strip()
        if not part:
            raise CronError(f"Bagian kosong di '{expr}'")
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) < 1:
                raise CronError(f"Langkah tidak valid: {step_text}")
            step = int(step_text)
        if part in ("*", "?"):
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            if not (a.isdigit() and b.isdigit()):
                raise CronError(f"Rentang tidak valid: {part}")
            start, end = int(a), int(b)
        elif part.isdigit():
            start = end = int(part)
        else:
            raise CronError(f"Nilai tidak valid: {part}")
        if start > end:
            raise CronError(f"Rentang terbalik: {part}")
        # check values are within bounds, else reject
        for value in range(start, end + 1, step):
            if value < lo or value > hi:
                raise CronError(f"Nilai {value} di luar rentang {lo}-{hi} di '{expr}'")
            out.add(value)
    return out


def _dow(expr: str) -> Set[int]:
    values = set()
    for value in _values(expr, 0, 7):
        values.add(0 if value == 7 else value)
    return values


def parse(expr: str) -> dict:
    parts = (expr or "").split()
    if len(parts) != 5:
        raise CronError("Format cron harus 5 bagian: menit jam tanggal bulan hari")
    return {
        "minute": _values(parts[0], 0, 59),
        "hour": _values(parts[1], 0, 23),
        "dom": _values(parts[2], 1, 31),
        "month": _values(parts[3], 1, 12),
        "dow": _dow(parts[4]),
    }


def validate(expr: str) -> str:
    parse(expr)
    return (expr or "").strip()


def matches(expr: str, moment: datetime) -> bool:
    parsed = parse(expr)
    cron_dow = (moment.weekday() + 1) % 7  # Python Senin=0 -> cron Minggu=0
    return (
        moment.minute in parsed["minute"]
        and moment.hour in parsed["hour"]
        and moment.day in parsed["dom"]
        and moment.month in parsed["month"]
        and cron_dow in parsed["dow"]
    )


def next_run(expr: str, now: datetime = None) -> datetime:
    """Waktu berikutnya (menit) yang cocok. Maksimal 366 hari ke depan."""
    current = (now or datetime.now()).replace(second=0, microsecond=0)
    for _ in range(366 * 24 * 60):
        current = datetime.fromtimestamp(current.timestamp() + 60)
        if matches(expr, current):
            return current
    raise CronError("Jadwal tidak pernah cocok dalam setahun")


def minute_key(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M")


def now() -> datetime:
    return datetime.fromtimestamp(time.time())


def make_job(schedule: str, prompt: str, channel_id: str, job_id: str = "") -> dict:
    """Bangun job tervalidasi. Melempar CronError bila tidak valid."""
    schedule = validate(schedule)
    prompt = (prompt or "").strip()
    if not prompt:
        raise CronError("Prompt cron wajib diisi.")
    channel_id = str(channel_id or "").strip().strip("<#>")
    if not channel_id.isdigit():
        raise CronError("Channel ID harus berupa angka.")
    stamp = int(time.time() * 1000) % 10**9
    return {
        "id": job_id or f"cron_{stamp}",
        "schedule": schedule,
        "prompt": prompt[:4000],
        "channel_id": channel_id,
        "enabled": True,
        "created_at": int(time.time()),
        "last_run": None,
        "last_status": "",
        "last_run_key": "",
    }


def sanitize_jobs(raw) -> list:
    """Rapikan daftar job dari config; buang yang rusak tanpa menggagalkan load."""
    jobs = []
    seen = set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        schedule = str(item.get("schedule") or "").strip()
        channel_id = str(item.get("channel_id") or "").strip().strip("<#>")
        prompt = str(item.get("prompt") or "").strip()
        try:
            schedule = validate(schedule)
        except CronError:
            continue
        if not channel_id.isdigit() or not prompt:
            continue
        job_id = str(item.get("id") or "") or f"cron_{int(time.time() * 1000) % 10**9}"
        if job_id in seen:
            continue
        seen.add(job_id)
        jobs.append(
            {
                "id": job_id,
                "schedule": schedule,
                "prompt": prompt[:4000],
                "channel_id": channel_id,
                "enabled": bool(item.get("enabled", True)),
                "created_at": int(item.get("created_at") or time.time()),
                "last_run": item.get("last_run"),
                "last_status": str(item.get("last_status") or "")[:300],
                "last_run_key": str(item.get("last_run_key") or ""),
            }
        )
    return jobs
