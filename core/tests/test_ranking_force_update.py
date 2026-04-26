from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import PerfilAluno


class RankingForceUpdateTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("core.services.ranking_refresh.snapshot_rankings_task")
    @patch("core.services.ranking_refresh.recompute_score_windows")
    @patch("core.services.ranking_refresh.fetch_student_data")
    def test_regular_user_refreshes_own_profile_and_recomputes_windows(
        self,
        fetch_student_data_mock,
        recompute_score_windows_mock,
        snapshot_rankings_task_mock,
    ):
        snapshot_rankings_task_mock.delay = Mock()
        user = User.objects.create_user(username="viewer", password="StrongPass123!")
        profile = PerfilAluno.objects.create(user=user)
        self.client.login(username="viewer", password="StrongPass123!")

        response = self.client.post(reverse("ranking_force_update"), HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sua conta foi sincronizada")
        fetch_student_data_mock.assert_called_once_with(profile.id)
        recompute_score_windows_mock.assert_called_once()
        snapshot_rankings_task_mock.delay.assert_called_once()
        self.assertIn("ranking-force-refresh", response.headers.get("HX-Trigger", ""))

    @override_settings(FORCE_RANKING_UPDATE_INLINE_MAX_STUDENTS_ADMIN=10)
    @patch("core.services.ranking_refresh.snapshot_rankings_task")
    @patch("core.services.ranking_refresh.recompute_score_windows")
    @patch("core.services.ranking_refresh.fetch_student_data")
    def test_staff_refreshes_all_profiles_inline_when_under_limit(
        self,
        fetch_student_data_mock,
        recompute_score_windows_mock,
        snapshot_rankings_task_mock,
    ):
        snapshot_rankings_task_mock.delay = Mock()
        admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="StrongPass123!",
        )
        other = User.objects.create_user(username="student", password="StrongPass123!")
        admin_profile = PerfilAluno.objects.create(user=admin, created_via="admin")
        other_profile = PerfilAluno.objects.create(user=other)
        self.client.login(username="admin", password="StrongPass123!")

        response = self.client.post(reverse("ranking_force_update"), HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ranking atualizado agora")
        self.assertEqual(fetch_student_data_mock.call_count, 2)
        fetch_student_data_mock.assert_any_call(admin_profile.id)
        fetch_student_data_mock.assert_any_call(other_profile.id)
        recompute_score_windows_mock.assert_called_once()
        snapshot_rankings_task_mock.delay.assert_called_once()

    @override_settings(FORCE_RANKING_UPDATE_INLINE_MAX_STUDENTS_ADMIN=0)
    @patch("core.services.ranking_refresh.snapshot_rankings_task")
    @patch("core.services.ranking_refresh.recompute_score_windows")
    @patch("core.services.ranking_refresh.sync_all_students")
    def test_staff_queues_global_refresh_when_over_inline_limit(
        self,
        sync_all_students_mock,
        recompute_score_windows_mock,
        snapshot_rankings_task_mock,
    ):
        sync_all_students_mock.delay = Mock()
        recompute_score_windows_mock.delay = Mock()
        snapshot_rankings_task_mock.delay = Mock()
        admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="StrongPass123!",
        )
        PerfilAluno.objects.create(user=admin, created_via="admin")
        self.client.login(username="admin", password="StrongPass123!")

        response = self.client.post(reverse("ranking_force_update"), HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Atualizacao global enfileirada")
        sync_all_students_mock.delay.assert_called_once()
        recompute_score_windows_mock.delay.assert_called_once()
        snapshot_rankings_task_mock.delay.assert_called_once()
