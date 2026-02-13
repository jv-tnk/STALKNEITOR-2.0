import logging
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

class CodeforcesClient:
    BASE_URL = "https://codeforces.com/api"

    @staticmethod
    def get_user_info(handle):
        if not handle:
            return {}

        url = f"{CodeforcesClient.BASE_URL}/user.info"
        params = {
            'handles': handle,
        }

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()

                if data.get('status') != 'OK':
                    comment = data.get('comment', '')
                    if 'limit' in comment.lower() and attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    logger.warning(f"Codeforces API error for {handle}: {comment}")
                    return {}

                result = data.get('result', [])
                if not result:
                    return {}

                payload = result[0]
                return {
                    'rating': payload.get('rating'),
                    'max_rating': payload.get('maxRating'),
                }

            except requests.RequestException as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                logger.error(f"Erro de conexão com Codeforces para {handle}: {e}")
                return {}
            except Exception as e:
                logger.error(f"Erro inesperado no parser do Codeforces: {e}")
                return {}

    @staticmethod
    def get_user_info_detailed(handle):
        if not handle:
            return None, "Sem handle."

        url = f"{CodeforcesClient.BASE_URL}/user.info"
        params = {
            'handles': handle,
        }

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()

                if data.get('status') != 'OK':
                    comment = data.get('comment', '') or 'Erro ao consultar Codeforces.'
                    if 'limit' in comment.lower() and attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    return None, comment

                result = data.get('result', [])
                if not result:
                    return None, "Usuário não encontrado no Codeforces."

                payload = result[0]
                rating = payload.get('rating')
                max_rating = payload.get('maxRating')
                return {
                    'rating': rating,
                    'max_rating': max_rating,
                }, None

            except requests.RequestException as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return None, f"Falha de conexão com Codeforces: {e}"
            except Exception as e:
                return None, f"Erro inesperado no parser do Codeforces: {e}"

    @staticmethod
    def get_submissions(handle, since=None, max_count=5000):
        """
        Busca todas as submissões do usuário no Codeforces.
        Retorna lista de dicionários com dados padronizados.
        """
        if not handle:
            return []

        url = f"{CodeforcesClient.BASE_URL}/user.status"
        params = {
            'handle': handle,
            'from': 1,
            'count': max_count,
        }

        since_ts = None
        if since:
            since_ts = int(since.timestamp())

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()

                if data.get('status') != 'OK':
                    comment = data.get('comment', '')
                    if 'limit' in comment.lower() and attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    logger.warning(f"Codeforces API error for {handle}: {comment}")
                    return []

                submissions = []
                for sub in data.get('result', []):
                    created_at = datetime.fromtimestamp(sub['creationTimeSeconds'], tz=timezone.utc)
                    if since_ts and sub['creationTimeSeconds'] <= since_ts:
                        break

                    problem = sub.get('problem', {})
                    if 'contestId' in problem and 'index' in problem:
                        submissions.append({
                            'platform': 'CF',
                            'contest_id': str(problem['contestId']),
                            'problem_index': problem['index'],
                            'problem_name': problem.get('name', ''),
                            'tags': ','.join(problem.get('tags', [])),
                            'verdict': sub.get('verdict', 'UNKNOWN'),
                            'submission_time': created_at,
                            'external_id': str(sub.get('id')),
                        })

                return submissions

            except requests.RequestException as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                logger.error(f"Erro de conexão com Codeforces para {handle}: {e}")
                return []
            except Exception as e:
                logger.error(f"Erro inesperado no parser do Codeforces: {e}")
                return []

    @staticmethod
    def get_contest_submissions(handle, contest_id, max_count=10000):
        """
        Busca submissões de um usuário em um contest específico via contest.status.
        """
        if not handle or not contest_id:
            return []

        url = f"{CodeforcesClient.BASE_URL}/contest.status"
        params = {
            "contestId": str(contest_id),
            "handle": handle,
            "from": 1,
            "count": max_count,
        }

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()

                if data.get("status") != "OK":
                    comment = data.get("comment", "")
                    if "limit" in comment.lower() and attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    logger.warning(
                        "Codeforces contest.status error handle=%s contest_id=%s: %s",
                        handle,
                        contest_id,
                        comment,
                    )
                    return []

                submissions = []
                for sub in data.get("result", []):
                    problem = sub.get("problem", {}) or {}
                    if str(problem.get("contestId") or "") != str(contest_id):
                        continue
                    if "index" not in problem:
                        continue
                    created_at = datetime.fromtimestamp(
                        sub["creationTimeSeconds"], tz=timezone.utc
                    )
                    submissions.append(
                        {
                            "platform": "CF",
                            "contest_id": str(problem.get("contestId") or contest_id),
                            "problem_index": problem["index"],
                            "problem_name": problem.get("name", ""),
                            "tags": ",".join(problem.get("tags", [])),
                            "verdict": sub.get("verdict", "UNKNOWN"),
                            "submission_time": created_at,
                            "external_id": str(sub.get("id")),
                        }
                    )
                return submissions

            except requests.RequestException as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                logger.error(
                    "Erro de conexão com Codeforces contest.status handle=%s contest_id=%s: %s",
                    handle,
                    contest_id,
                    e,
                )
                return []
            except Exception as e:
                logger.error(
                    "Erro inesperado no parser do Codeforces contest.status handle=%s contest_id=%s: %s",
                    handle,
                    contest_id,
                    e,
                )
                return []


    @staticmethod
    def get_rating_changes(handle):
        """
        Busca o histórico de mudanças de rating (CF) via user.rating.
        Retorna lista de dicts com:
          contest_id, contest_name, rating_old, rating_new, rating_update_time (datetime UTC)
        """
        if not handle:
            return []

        url = f"{CodeforcesClient.BASE_URL}/user.rating"
        params = {"handle": handle}

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()

                if data.get("status") != "OK":
                    comment = data.get("comment", "")
                    if "limit" in comment.lower() and attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    logger.warning(f"Codeforces API error (user.rating) for {handle}: {comment}")
                    return []

                results = []
                for row in data.get("result", []) or []:
                    ts = row.get("ratingUpdateTimeSeconds")
                    if not ts:
                        continue
                    results.append({
                        "contest_id": str(row.get("contestId") or ""),
                        "contest_name": row.get("contestName") or "",
                        "rating_old": row.get("oldRating"),
                        "rating_new": row.get("newRating"),
                        "rating_update_time": datetime.fromtimestamp(ts, tz=timezone.utc),
                    })

                return results

            except requests.RequestException as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                logger.error(f"Erro de conexão com Codeforces (user.rating) para {handle}: {e}")
                return []
            except Exception as e:
                logger.error(f"Erro inesperado no parser do Codeforces (user.rating): {e}")
                return []


class AtCoderClient:
    BASE_URL = "https://kenkoooo.com/atcoder/atcoder-api/v3"
    ATCODER_WEB_URL = "https://atcoder.jp"

    @staticmethod
    def get_user_info(handle):
        info, error = AtCoderClient.get_user_info_detailed(handle)
        if error or not info:
            return {}
        return info

    @staticmethod
    def get_user_info_detailed(handle):
        if not handle:
            return None, "Sem handle."

        url = f"{AtCoderClient.BASE_URL}/user/info"
        params = {
            'user': handle,
        }

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=params, timeout=10)
                if response.status_code == 404:
                    info2, err2 = AtCoderClient._get_user_info_from_official(handle)
                    if info2:
                        return info2, None
                    return None, err2 or "Usuário não encontrado no AtCoder (Kenkoooo)."
                response.raise_for_status()
                data = response.json()

                rating = data.get('rating')
                max_rating = data.get('highest_rating')
                if max_rating is None:
                    max_rating = rating

                info = {
                    'rating': rating,
                    'max_rating': max_rating,
                }

                if rating is None:
                    info2, err2 = AtCoderClient._get_user_info_from_official(handle)
                    if info2:
                        return info2, None
                    return None, err2 or "Usuário sem rating no Kenkoooo."

                return info, None

            except requests.RequestException as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                info2, err2 = AtCoderClient._get_user_info_from_official(handle)
                if info2:
                    return info2, None
                if err2:
                    return None, f"Kenkoooo: {e}. AtCoder: {err2}"
                return None, f"Falha de conexão com AtCoder: {e}"
            except Exception as e:
                info2, err2 = AtCoderClient._get_user_info_from_official(handle)
                if info2:
                    return info2, None
                if err2:
                    return None, f"Kenkoooo: {e}. AtCoder: {err2}"
                return None, f"Erro inesperado no parser do AtCoder: {e}"

    @staticmethod
    def _get_user_info_from_official(handle):
        """
        Fallback para o endpoint oficial do AtCoder (histórico JSON).
        """
        if not handle:
            return None, "Sem handle."

        url = f"{AtCoderClient.ATCODER_WEB_URL}/users/{handle}/history/json"
        max_retries = 2
        for attempt in range(max_retries):
            try:
                response = requests.get(url, timeout=10)
                if response.status_code == 404:
                    return None, "Usuário não encontrado no AtCoder."
                response.raise_for_status()
                data = response.json()
                if not data:
                    return None, "Usuário sem histórico de rating."

                latest = data[-1]
                rating = latest.get("NewRating")
                ratings = [row.get("NewRating") for row in data if row.get("NewRating") is not None]
                max_rating = max(ratings) if ratings else rating

                if rating is None:
                    return None, "Histórico sem rating válido."

                return {
                    "rating": rating,
                    "max_rating": max_rating or rating,
                }, None

            except requests.RequestException as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return None, f"Falha ao consultar AtCoder oficial: {e}"
            except Exception as e:
                return None, f"Erro ao processar histórico do AtCoder: {e}"

    @staticmethod
    def get_submissions(handle, since=None):
        """
        Busca submissões do usuário no AtCoder via API do Kenkoooo.
        """
        if not handle:
            return []

        from_second = 0
        if since:
            from_second = max(int(since.timestamp()) - 1, 0)

        url = f"{AtCoderClient.BASE_URL}/user/submissions"
        params = {
            'user': handle,
            'from_second': from_second,
        }

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=params, timeout=10)
                if response.status_code == 403:
                    logger.warning(
                        "AtCoder (Kenkoooo) retornou 403 para %s. "
                        "Endpoint de submissões indisponível; retornando lista vazia.",
                        handle,
                    )
                    return []
                response.raise_for_status()
                raw_subs = response.json()

                submissions = []
                for sub in raw_subs:
                    submissions.append({
                        'platform': 'AC',
                        'contest_id': sub['contest_id'],
                        'problem_index': sub['problem_id'].split('_')[-1].upper(),
                        'problem_id': sub['problem_id'],
                        'verdict': sub.get('result', 'UNKNOWN'),
                        'submission_time': datetime.fromtimestamp(sub['epoch_second'], tz=timezone.utc),
                        'external_id': str(sub['id']),
                    })

                return submissions

            except requests.RequestException as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                logger.error(f"Erro de conexão com AtCoder (Kenkoooo) para {handle}: {e}")
                return []
            except Exception as e:
                logger.error(f"Erro inesperado no parser do AtCoder: {e}")
                return []


def _get_solved_from_db(student, plataforma):
    from core.models import Submissao

    solved_ids = set()
    subs = Submissao.objects.filter(
        aluno=student,
        plataforma=plataforma,
        verdict__in=['OK', 'AC'],
    ).values_list('contest_id', 'problem_index')

    for contest_id, problem_index in subs:
        if not contest_id or not problem_index:
            continue
        if plataforma == 'CF':
            solved_ids.add(f"{contest_id}{problem_index}")
        elif plataforma == 'AC':
            solved_ids.add(f"{contest_id}_{problem_index.lower()}")

    return solved_ids


def get_all_solved_problems(cf_handle=None, ac_handle=None, student=None, prefer_db=True):
    """
    Mantido para retrocompatibilidade com views existentes que esperam apenas IDs.
    """
    solved_ids = set()

    if student and prefer_db:
        if cf_handle:
            if student.submissoes.filter(plataforma='CF').exists():
                solved_ids.update(_get_solved_from_db(student, 'CF'))
            else:
                subs = CodeforcesClient.get_submissions(cf_handle)
                for s in subs:
                    if s['verdict'] == 'OK':
                        solved_ids.add(f"{s['contest_id']}{s['problem_index']}")
        if ac_handle:
            if student.submissoes.filter(plataforma='AC').exists():
                solved_ids.update(_get_solved_from_db(student, 'AC'))
            else:
                subs = AtCoderClient.get_submissions(ac_handle)
                for s in subs:
                    if s['verdict'] == 'AC':
                        solved_ids.add(s['problem_id'])
        return solved_ids

    if cf_handle:
        subs = CodeforcesClient.get_submissions(cf_handle)
        for s in subs:
            if s['verdict'] == 'OK':
                solved_ids.add(f"{s['contest_id']}{s['problem_index']}")

    if ac_handle:
        subs = AtCoderClient.get_submissions(ac_handle)
        for s in subs:
            if s['verdict'] == 'AC':
                solved_ids.add(s['problem_id'])

    return solved_ids
