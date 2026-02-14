from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import PerfilAluno, SolucaoCompartilhada


class PermissionRegressionTests(TestCase):
    def setUp(self):
        self.owner_user = User.objects.create_user(username="owner", password="StrongPass123!")
        self.owner_profile = PerfilAluno.objects.create(user=self.owner_user)

        self.viewer_user = User.objects.create_user(username="viewer", password="StrongPass123!")
        self.viewer_profile = PerfilAluno.objects.create(user=self.viewer_user)

        self.admin_user = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="StrongPass123!",
        )
        PerfilAluno.objects.create(user=self.admin_user, created_via="admin")

    def _create_solution(self, slug: str, *, visibility: str, status: str) -> SolucaoCompartilhada:
        long_summary = "Resumo da ideia " + ("x" * 120)
        long_code = ("int main(){return 0;}\n" * 10).strip()
        return SolucaoCompartilhada.objects.create(
            aluno=self.owner_profile,
            problem_url=f"https://codeforces.com/contest/1/problem/{slug}",
            platform_context="CF",
            language="cpp",
            visibility=visibility,
            status=status,
            idea_summary=long_summary,
            code_text=long_code,
        )

    def test_add_student_requires_superuser(self):
        self.client.login(username="viewer", password="StrongPass123!")

        response_get = self.client.get(reverse("add_student"))
        self.assertEqual(response_get.status_code, 403)

        response_post = self.client.post(
            reverse("add_student"),
            {
                "action": "create_student",
                "username": "new_student",
            },
        )
        self.assertEqual(response_post.status_code, 403)
        self.assertFalse(User.objects.filter(username="new_student").exists())

    def test_add_student_creates_non_default_password(self):
        self.client.login(username="admin", password="StrongPass123!")

        response = self.client.post(
            reverse("add_student"),
            {
                "action": "create_student",
                "username": "new_student",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Senha temporaria:")
        created_user = User.objects.get(username="new_student")
        self.assertFalse(created_user.check_password("password123"))

    def test_solutions_view_blocks_unshared_or_private(self):
        private_published = self._create_solution("A", visibility="private", status="published")
        public_draft = self._create_solution("B", visibility="public", status="draft")

        self.client.login(username="viewer", password="StrongPass123!")
        private_response = self.client.get(
            f"{reverse('solutions_view')}?solution_id={private_published.id}"
        )
        draft_response = self.client.get(
            f"{reverse('solutions_view')}?solution_id={public_draft.id}"
        )

        self.assertEqual(private_response.status_code, 404)
        self.assertEqual(draft_response.status_code, 404)

    def test_solutions_view_allows_published_shared(self):
        shared_public = self._create_solution("C", visibility="public", status="published")
        shared_class = self._create_solution("D", visibility="class", status="published")

        self.client.login(username="viewer", password="StrongPass123!")
        public_response = self.client.get(
            f"{reverse('solutions_view')}?solution_id={shared_public.id}"
        )
        class_response = self.client.get(
            f"{reverse('solutions_view')}?solution_id={shared_class.id}"
        )

        self.assertEqual(public_response.status_code, 200)
        self.assertEqual(class_response.status_code, 200)

    def test_solutions_view_owner_still_can_access_private_draft(self):
        private_draft = self._create_solution("E", visibility="private", status="draft")

        self.client.login(username="owner", password="StrongPass123!")
        response = self.client.get(f"{reverse('solutions_view')}?solution_id={private_draft.id}")

        self.assertEqual(response.status_code, 200)
