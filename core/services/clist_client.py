from typing import Any

import requests
from django.conf import settings


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
        response = requests.get(url, params=params, timeout=timeout)

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
    def _extract_problem(cls, payload: dict[str, Any]) -> dict[str, Any] | None:
        objects = payload.get("objects") or []
        if not objects:
            return None

        for obj in objects:
            if obj.get("rating") is not None:
                return obj

        return objects[0]

    @classmethod
    def fetch_problem_rating(
        cls,
        platform: str,
        problem_url: str,
        problem_name: str | None = None,
    ) -> dict[str, Any]:
        params = {"url": problem_url, "format": "json"}
        params.update(cls._auth_params())

        result = cls._request(params)
        if result.get("status") == "TEMP_FAIL":
            return result

        obj = cls._extract_problem(result.get("payload", {}))
        if obj:
            return {
                "status": "OK",
                "rating": obj.get("rating"),
                "problem_id": obj.get("id"),
            }

        if problem_name:
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

                obj = cls._extract_problem(fallback_result.get("payload", {}))
                if obj:
                    return {
                        "status": "OK",
                        "rating": obj.get("rating"),
                        "problem_id": obj.get("id"),
                    }

        return {"status": "NOT_FOUND"}
