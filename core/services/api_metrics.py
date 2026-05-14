"""Lightweight API call metrics stored in Redis (best-effort, never breaks callers)."""
import json
import logging
import time

import requests

logger = logging.getLogger(__name__)

_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        from django.conf import settings
        import redis
        url = getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0")
        _redis_client = redis.Redis.from_url(url)
    return _redis_client


def _infer_api_name(url: str) -> str:
    """Infer a human-readable API name from the request URL."""
    url_lower = (url or "").lower()
    if "codeforces.com" in url_lower:
        if "/user.status" in url_lower:
            return "cf.submissions"
        if "/contest.status" in url_lower:
            return "cf.contest_subs"
        if "/user.info" in url_lower:
            return "cf.user_info"
        if "/user.rating" in url_lower:
            return "cf.rating_changes"
        if "/contest.list" in url_lower:
            return "cf.contest_list"
        if "/contest.standings" in url_lower:
            return "cf.contest_standings"
        if "/problemset.problems" in url_lower:
            return "cf.problemset"
        return "cf.other"
    if "kenkoooo.com" in url_lower:
        if "submissions" in url_lower:
            return "ac.submissions"
        if "user/info" in url_lower or "user_info" in url_lower:
            return "ac.user_info"
        if "contests" in url_lower:
            return "ac.contests"
        if "problems" in url_lower:
            return "ac.problems"
        return "ac.other"
    if "atcoder.jp" in url_lower:
        if "history" in url_lower:
            return "ac.user_info_official"
        if "/tasks" in url_lower:
            return "ac.tasks_html"
        return "ac.other"
    if "clist.by" in url_lower:
        return "clist.problem"
    return "unknown"


def _record_api_metric(api_name: str, latency_ms: int, status_code: int = 0) -> None:
    """Append a metric entry to the rolling window for api_name."""
    try:
        client = _get_redis()
        entry = json.dumps({"t": round(time.time(), 1), "ms": latency_ms, "sc": status_code})
        key = f"api_metrics:{api_name}"
        pipe = client.pipeline(transaction=False)
        pipe.lpush(key, entry)
        pipe.ltrim(key, 0, 99)
        pipe.expire(key, 86400)
        pipe.execute()
    except Exception:
        pass


def tracked_get(url: str, **kwargs) -> requests.Response:
    """Drop-in replacement for requests.get() that records timing metrics."""
    api_name = _infer_api_name(url)
    started = time.monotonic()
    status_code = 0
    try:
        resp = requests.get(url, **kwargs)
        status_code = resp.status_code
        return resp
    except requests.RequestException:
        raise
    finally:
        latency_ms = int((time.monotonic() - started) * 1000)
        _record_api_metric(api_name, latency_ms, status_code)


def get_all_api_metrics() -> list[dict]:
    """Read metrics summaries for all tracked APIs. Used by admin dashboard."""
    try:
        client = _get_redis()
        keys = list(client.scan_iter(match="api_metrics:*", count=200))
    except Exception:
        return []

    now = time.time()
    one_hour_ago = now - 3600
    results = []

    for key in sorted(keys):
        api_name = key.decode().removeprefix("api_metrics:")
        try:
            raw_entries = client.lrange(key, 0, 99)
        except Exception:
            continue

        entries = []
        for raw in raw_entries:
            try:
                entries.append(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                continue

        if not entries:
            continue

        recent = [e for e in entries if e.get("t", 0) >= one_hour_ago]
        total = len(recent)
        if not total:
            recent = entries[:10]
            total = len(recent)
            window_label = "ultimas"
        else:
            window_label = "1h"

        errors = sum(1 for e in recent if not (200 <= (e.get("sc") or 0) < 400))
        latencies = [e.get("ms", 0) for e in recent]
        avg_ms = int(sum(latencies) / len(latencies)) if latencies else 0
        sorted_lat = sorted(latencies)
        p95_idx = min(int(len(sorted_lat) * 0.95), len(sorted_lat) - 1)
        p95_ms = sorted_lat[p95_idx] if sorted_lat else 0
        last_call = max((e.get("t", 0) for e in entries), default=0)
        last_call_ago = int(now - last_call) if last_call else None

        results.append({
            "api_name": api_name,
            "calls": total,
            "window": window_label,
            "errors": errors,
            "error_rate": round(errors / total * 100, 1) if total else 0,
            "avg_ms": avg_ms,
            "p95_ms": p95_ms,
            "last_call_ago_s": last_call_ago,
            "last_call_ago_human": _humanize_seconds(last_call_ago) if last_call_ago is not None else None,
        })

    return results


def _humanize_seconds(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}min"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"
