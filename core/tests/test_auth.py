from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User

from core.models import PerfilAluno


class AuthFlowTests(TestCase):
    def test_signup_creates_user_and_profile(self):
        response = self.client.post(reverse('signup'), {
            'username': 'alice',
            'password1': 'StrongPass123!',
            'password2': 'StrongPass123!',
            'cf_handle': 'alice_cf',
            'ac_handle': 'alice_ac',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username='alice').exists())
        user = User.objects.get(username='alice')
        self.assertTrue(PerfilAluno.objects.filter(user=user).exists())

    def test_login_works(self):
        user = User.objects.create_user(username='bob', password='StrongPass123!')
        PerfilAluno.objects.create(user=user)
        response = self.client.post(reverse('login'), {
            'username': 'bob',
            'password': 'StrongPass123!',
        })
        self.assertEqual(response.status_code, 302)

    def test_me_requires_login(self):
        response = self.client.get(reverse('me'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_login_creates_missing_profile_for_superuser(self):
        admin = User.objects.create_superuser(
            username='root',
            email='root@example.com',
            password='StrongPass123!',
        )
        self.assertFalse(PerfilAluno.objects.filter(user=admin).exists())

        response = self.client.post(reverse('login'), {
            'username': 'root',
            'password': 'StrongPass123!',
        })

        self.assertEqual(response.status_code, 302)
        profile = PerfilAluno.objects.get(user=admin)
        self.assertEqual(profile.created_via, 'admin')

    def test_update_handles(self):
        user = User.objects.create_user(username='carol', password='StrongPass123!')
        profile = PerfilAluno.objects.create(user=user)
        self.client.login(username='carol', password='StrongPass123!')
        response = self.client.post(reverse('me'), {
            'cf_handle': 'carol_cf',
            'ac_handle': 'carol_ac',
        })
        self.assertEqual(response.status_code, 200)
        profile.refresh_from_db()
        self.assertEqual(profile.handle_codeforces, 'carol_cf')
        self.assertEqual(profile.handle_atcoder, 'carol_ac')
