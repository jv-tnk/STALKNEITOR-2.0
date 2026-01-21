from functools import lru_cache

import requests


CF_API_BASE = "https://codeforces.com/api"
AC_CONTEST_PROBLEM_URL = "https://kenkoooo.com/atcoder/resources/contest-problem.json"
AC_PROBLEMS_URL = "https://kenkoooo.com/atcoder/resources/problems.json"


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
    results = []
    for problem in problems:
        index = problem.get("index")
        name = problem.get("name") or index
        tags = problem.get("tags") or []
        if not index:
            continue
        results.append(
            {
                "index": str(index),
                "name": name,
                "tags": tags,
            }
        )
    return results


@lru_cache(maxsize=1)
def _load_ac_resources() -> tuple[dict[str, list[str]], dict[str, str]]:
    contest_map: dict[str, list[str]] = {}
    problem_titles: dict[str, str] = {}

    contest_resp = requests.get(AC_CONTEST_PROBLEM_URL, timeout=10)
    contest_resp.raise_for_status()
    for row in contest_resp.json():
        contest_id = row.get("contest_id")
        problem_id = row.get("problem_id")
        if not contest_id or not problem_id:
            continue
        contest_map.setdefault(contest_id, []).append(problem_id)

    problems_resp = requests.get(AC_PROBLEMS_URL, timeout=10)
    problems_resp.raise_for_status()
    for row in problems_resp.json():
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
