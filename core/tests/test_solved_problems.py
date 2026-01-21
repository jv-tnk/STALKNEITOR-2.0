from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from core.models import PerfilAluno, Submissao
from core.services.api_client import get_all_solved_problems


class SolvedProblemsTests(TestCase):
    def test_get_all_solved_problems_prefers_db(self):
        user = User.objects.create_user(username='alice', password='testpass')
        student = PerfilAluno.objects.create(
            user=user,
            handle_codeforces='alice_cf',
            handle_atcoder='alice_ac',
        )

        Submissao.objects.create(
            aluno=student,
            plataforma='CF',
            contest_id='1234',
            problem_index='A',
            verdict='OK',
            submission_time=timezone.now(),
            external_id='cf-1',
        )
        Submissao.objects.create(
            aluno=student,
            plataforma='AC',
            contest_id='abc001',
            problem_index='B',
            verdict='AC',
            submission_time=timezone.now(),
            external_id='ac-1',
        )

        solved = get_all_solved_problems(
            student.handle_codeforces,
            student.handle_atcoder,
            student=student,
            prefer_db=True,
        )

        self.assertIn('1234A', solved)
        self.assertIn('abc001_b', solved)
