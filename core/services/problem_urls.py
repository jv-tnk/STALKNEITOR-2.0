from urllib.parse import urlsplit, urlunsplit


def normalize_problem_url(url: str) -> str:
    if not url:
        return url
    parts = urlsplit(url.strip())
    cleaned_path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, cleaned_path, "", ""))


def build_problem_url_from_fields(
    platform: str,
    contest_id: str | None,
    problem_index: str | None,
    problem_name: str | None = None,
) -> str | None:
    if platform == "CF":
        if not contest_id or not problem_index:
            return None
        return normalize_problem_url(
            f"https://codeforces.com/contest/{contest_id}/problem/{problem_index}"
        )

    if platform == "AC":
        if not contest_id or not problem_index:
            return None
        task_id = problem_name
        if not task_id or "_" not in task_id:
            task_id = f"{contest_id}_{problem_index.lower()}"
        return normalize_problem_url(
            f"https://atcoder.jp/contests/{contest_id}/tasks/{task_id}"
        )

    return None
