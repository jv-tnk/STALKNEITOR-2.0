from datetime import datetime, timezone
from functools import lru_cache

import requests


CF_API_BASE = "https://codeforces.com/api"
CF_CONTEST_LIST_URL = f"{CF_API_BASE}/contest.list"
CF_PROBLEMSET_URL = f"{CF_API_BASE}/problemset.problems"
AC_CONTEST_PROBLEM_URLS = (
    "https://kenkoooo.com/atcoder/resources/contest-problem.json",
    "https://s3.ap-northeast-1.amazonaws.com/kenkoooo.com/resources/contest-problem.json",
)
AC_PROBLEMS_URLS = (
    "https://kenkoooo.com/atcoder/resources/problems.json",
    "https://s3.ap-northeast-1.amazonaws.com/kenkoooo.com/resources/problems.json",
)
AC_CONTESTS_URLS = (
    "https://kenkoooo.com/atcoder/resources/contests.json",
    "https://s3.ap-northeast-1.amazonaws.com/kenkoooo.com/resources/contests.json",
)


def _fetch_json_with_fallback(urls: tuple[str, ...], timeout: int = 10):
    last_error = None
    for url in urls:
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    raise requests.RequestException("Nenhum endpoint de fallback disponível.")


def get_cf_contests(year: int) -> list[dict]:
    if not year:
        return []

    try:
        response = requests.get(CF_CONTEST_LIST_URL, timeout=10)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        return []
    except ValueError:
        return []

    if payload.get("status") != "OK":
        return []

    results = []
    for contest in payload.get("result", []):
        start_ts = contest.get("startTimeSeconds")
        if not start_ts:
            continue
        start_time = datetime.fromtimestamp(start_ts, tz=timezone.utc)
        if start_time.year != year:
            continue
        contest_id = contest.get("id")
        if contest_id is None:
            continue
        results.append(
            {
                "contest_id": str(contest_id),
                "title": contest.get("name") or str(contest_id),
                "start_time": start_time,
                "duration_seconds": contest.get("durationSeconds"),
                "phase": contest.get("phase") or "",
                "is_gym": bool(contest.get("type") == "Gym" or contest.get("type") == "gym"),
            }
        )
    results.sort(key=lambda row: row["start_time"])
    return results


def get_ac_contests(year: int) -> list[dict]:
    if not year:
        return []

    try:
        payload = _fetch_json_with_fallback(AC_CONTESTS_URLS, timeout=10)
    except requests.RequestException:
        return []
    except ValueError:
        return []

    results = []
    for contest in payload:
        start_ts = contest.get("start_epoch_second")
        if not start_ts:
            continue
        start_time = datetime.fromtimestamp(start_ts, tz=timezone.utc)
        if start_time.year != year:
            continue
        contest_id = contest.get("id")
        if not contest_id:
            continue
        results.append(
            {
                "contest_id": str(contest_id),
                "title": contest.get("title") or contest_id,
                "start_time": start_time,
                "duration_seconds": contest.get("duration_second"),
            }
        )
    results.sort(key=lambda row: row["start_time"])
    return results


def get_cf_contest_problems(contest_id: str) -> list[dict]:
    if not contest_id:
        return []

    url = f"{CF_API_BASE}/contest.standings"
    params = {"contestId": contest_id, "from": 1, "count": 1}
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        return []
    except ValueError:
        return []

    if payload.get("status") != "OK":
        return []

    problems = payload.get("result", {}).get("problems", []) or []
    cf_map = _get_cf_problemset_map()
    results = []
    for problem in problems:
        index = problem.get("index")
        name = problem.get("name") or index
        tags = problem.get("tags") or []
        rating = None
        if not index:
            continue
        contest_key = str(contest_id)
        map_key = f"{contest_key}:{index}"
        if map_key in cf_map:
            mapped = cf_map[map_key]
            if mapped.get("tags"):
                tags = mapped["tags"]
            rating = mapped.get("rating")
        results.append(
            {
                "index": str(index),
                "name": name,
                "tags": tags,
                "rating": rating,
            }
        )
    return results


@lru_cache(maxsize=1)
def _get_cf_problemset_map() -> dict[str, dict]:
    """
    Build a map for (contestId:index) -> {rating, tags}
    """
    try:
        response = requests.get(CF_PROBLEMSET_URL, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        return {}
    except ValueError:
        return {}

    if payload.get("status") != "OK":
        return {}

    problems = payload.get("result", {}).get("problems", []) or []
    mapped: dict[str, dict] = {}
    for problem in problems:
        contest_id = problem.get("contestId")
        index = problem.get("index")
        if not contest_id or not index:
            continue
        key = f"{contest_id}:{index}"
        mapped[key] = {
            "rating": problem.get("rating"),
            "tags": problem.get("tags") or [],
        }
    return mapped


@lru_cache(maxsize=1)
def _load_ac_resources() -> tuple[dict[str, list[str]], dict[str, str]]:
    contest_map: dict[str, list[str]] = {}
    problem_titles: dict[str, str] = {}

    contest_payload = _fetch_json_with_fallback(AC_CONTEST_PROBLEM_URLS, timeout=10)
    for row in contest_payload:
        contest_id = row.get("contest_id")
        problem_id = row.get("problem_id")
        if not contest_id or not problem_id:
            continue
        contest_map.setdefault(contest_id, []).append(problem_id)

    problems_payload = _fetch_json_with_fallback(AC_PROBLEMS_URLS, timeout=10)
    for row in problems_payload:
        problem_id = row.get("id")
        title = row.get("title")
        if problem_id and title:
            problem_titles[problem_id] = title

    return contest_map, problem_titles


def get_ac_contest_problems(contest_id: str) -> list[dict]:
    if not contest_id:
        return []

    try:
        contest_map, titles = _load_ac_resources()
    except requests.RequestException:
        return []
    except ValueError:
        return []

    problem_ids = contest_map.get(contest_id, [])
    results = []
    for problem_id in problem_ids:
        if not problem_id:
            continue
        index = problem_id.split("_")[-1].upper()
        title = titles.get(problem_id, problem_id)
        results.append(
            {
                "index": index,
                "name": title,
                "problem_id": problem_id,
                "tags": [],
            }
        )
    return results
