from typing import Any, Callable

import requests
from django.conf import settings
from urllib.parse import urlsplit
import re


class ClistClient:
    RESOURCE_REGEX = {
        "CF": "codeforces",
        "AC": "atcoder.jp",
    }

    @classmethod
    def _auth_params(cls) -> dict[str, str]:
        username = getattr(settings, "CLIST_USERNAME", "")
        api_key = getattr(settings, "CLIST_API_KEY", "")
        if username and api_key:
            return {"username": username, "api_key": api_key}
        return {}

    @classmethod
    def _request(cls, params: dict[str, Any]) -> dict[str, Any]:
        base_url = getattr(settings, "CLIST_API_URL", "https://clist.by/api/v4").rstrip("/")
        url = f"{base_url}/problem/"
        timeout = getattr(settings, "CLIST_TIMEOUT_SECONDS", 10)
        try:
            response = requests.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            return {
                "status": "TEMP_FAIL",
                "error": str(exc),
            }

        if response.status_code == 429 or response.status_code >= 500:
            return {
                "status": "TEMP_FAIL",
                "status_code": response.status_code,
            }

        if response.status_code != 200:
            return {
                "status": "TEMP_FAIL",
                "status_code": response.status_code,
            }

        try:
            payload = response.json()
        except ValueError:
            return {"status": "TEMP_FAIL", "status_code": response.status_code}

        return {"status": "OK", "payload": payload}

    @classmethod
    def _extract_problem(
        cls,
        payload: dict[str, Any],
        matcher: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any] | None:
        objects = payload.get("objects") or []
        if not objects:
            return None

        for obj in objects:
            if obj.get("rating") is None:
                continue
            if matcher and not matcher(obj):
                continue
            return obj

        # CLIST sometimes returns the problem object even when "rating" is null.
        # For our use-case (difficulty), treat this as "no rating available".
        return None

    @classmethod
    def _normalize_url(cls, url: str) -> str:
        parts = urlsplit((url or "").strip())
        path = parts.path.rstrip("/")
        return f"{parts.scheme}://{parts.netloc}{path}" if parts.scheme and parts.netloc else (url or "").strip()

    @classmethod
    def _parse_cf_problem_url(cls, url: str | None) -> tuple[str, str] | None:
        if not url:
            return None
        path = urlsplit(url).path.rstrip("/")
        patterns = [
            r"^/contest/(?P<contest_id>\d+)/problem/(?P<index>[A-Za-z][A-Za-z0-9]*)$",
            r"^/problemset/problem/(?P<contest_id>\d+)/(?P<index>[A-Za-z][A-Za-z0-9]*)$",
            r"^/gym/(?P<contest_id>\d+)/problem/(?P<index>[A-Za-z][A-Za-z0-9]*)$",
        ]
        for pattern in patterns:
            match = re.match(pattern, path, flags=re.IGNORECASE)
            if match:
                return match.group("contest_id"), match.group("index").upper()
        return None

    @classmethod
    def _cf_url_candidates(cls, problem_url: str) -> list[str]:
        normalized = cls._normalize_url(problem_url)
        parsed = cls._parse_cf_problem_url(normalized)
        if not parsed:
            return [normalized]
        contest_id, index = parsed
        candidates = [
            normalized,
            f"https://codeforces.com/contest/{contest_id}/problem/{index}",
            f"https://codeforces.com/problemset/problem/{contest_id}/{index}",
            f"https://codeforces.com/problemset/problem/{contest_id}/{index.lower()}",
        ]
        deduped = []
        seen = set()
        for candidate in candidates:
            key = cls._normalize_url(candidate)
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(key)
        return deduped

    @classmethod
    def _build_matcher(cls, platform: str, requested_problem_url: str):
        platform = (platform or "").upper()
        if platform == "CF":
            parsed_requested = cls._parse_cf_problem_url(requested_problem_url)
            if parsed_requested:
                contest_id, index = parsed_requested

                def _matcher(obj: dict[str, Any]) -> bool:
                    parsed_obj = cls._parse_cf_problem_url(obj.get("url") or "")
                    if not parsed_obj:
                        return False
                    return parsed_obj[0] == contest_id and parsed_obj[1] == index

                return _matcher
        return None

    @classmethod
    def fetch_problem_rating(
        cls,
        platform: str,
        problem_url: str,
        problem_name: str | None = None,
    ) -> dict[str, Any]:
        platform = (platform or "").upper()
        normalized_problem_url = cls._normalize_url(problem_url)
        matcher = cls._build_matcher(platform, normalized_problem_url)

        url_candidates = [normalized_problem_url]
        if platform == "CF":
            url_candidates = cls._cf_url_candidates(normalized_problem_url)

        for candidate_url in url_candidates:
            params = {"url": candidate_url, "format": "json"}
            params.update(cls._auth_params())

            result = cls._request(params)
            if result.get("status") == "TEMP_FAIL":
                return result

            obj = cls._extract_problem(result.get("payload", {}), matcher=matcher)
            if obj and obj.get("rating") is not None:
                return {
                    "status": "OK",
                    "rating": obj.get("rating"),
                    "problem_id": obj.get("id"),
                }

        # For CF, fallback by "name" is ambiguous when rounds are split by division
        # (e.g. same round number with Div.1/Div.2). Prefer strict URL matching only.
        if problem_name and platform != "CF":
            regex = cls.RESOURCE_REGEX.get(platform)
            if regex:
                fallback_params = {
                    "name": problem_name,
                    "resource__regex": regex,
                    "format": "json",
                }
                fallback_params.update(cls._auth_params())
                fallback_result = cls._request(fallback_params)
                if fallback_result.get("status") == "TEMP_FAIL":
                    return fallback_result

                obj = cls._extract_problem(fallback_result.get("payload", {}), matcher=matcher)
                if obj and obj.get("rating") is not None:
                    return {
                        "status": "OK",
                        "rating": obj.get("rating"),
                        "problem_id": obj.get("id"),
                    }

        return {"status": "NOT_FOUND"}
