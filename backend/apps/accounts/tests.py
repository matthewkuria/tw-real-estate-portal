import re

from django.core import mail
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import MemberProfile, User


PASSWORD = "Old-pass-123!"
NEW_PASSWORD = "New-pass-456!"

# The default hasher is deliberately slow; these tests hash many passwords.
fast_hashing = override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)


def make_user(email="member@example.com", role=User.Role.MEMBER,
              status=User.Status.ACTIVE, is_active=True, **extra):
    return User.objects.create_user(
        email=email,
        password=PASSWORD,
        role=role,
        status=status,
        is_active=is_active,
        **extra,
    )


@fast_hashing
class AuthTestCase(APITestCase):
    """
    Base class with helpers for signing in through the API.
    """

    def login(self, email, password=PASSWORD):
        return self.client.post(
            reverse("accounts:login"),
            {"email": email, "password": password},
            format="json",
        )

    def tokens_for(self, email, password=PASSWORD):
        response = self.login(email, password)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.json()["data"]

    def authenticate(self, user):
        tokens = self.tokens_for(user.email)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {tokens['access']}"
        )
        return tokens

    def refresh(self, refresh_token):
        return self.client.post(
            reverse("accounts:token-refresh"),
            {"refresh": refresh_token},
            format="json",
        )


# ============================================================================
# REGISTRATION
# ============================================================================

@fast_hashing
class RegistrationTests(APITestCase):
    url = reverse("accounts:register")

    def payload(self, **overrides):
        data = {
            "email": "New.Member@Example.com",
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
            "first_name": "Jane",
            "last_name": "Doe",
            "phone_number": "+254712345678",
        }
        data.update(overrides)
        return data

    def test_creates_pending_inactive_member_with_profile(self):
        response = self.client.post(self.url, self.payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()["code"], "S001")

        user = User.objects.get(email="new.member@example.com")
        self.assertEqual(user.role, User.Role.MEMBER)
        self.assertEqual(user.status, User.Status.PENDING)
        self.assertFalse(user.is_active)
        self.assertEqual(user.member_profile.phone_number, "+254712345678")

    def test_phone_number_is_optional(self):
        payload = self.payload()
        del payload["phone_number"]

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email="new.member@example.com")
        self.assertEqual(user.member_profile.phone_number, "")

    def test_cannot_choose_own_role_or_status(self):
        response = self.client.post(
            self.url,
            self.payload(role=User.Role.SUPER_ADMIN, status=User.Status.ACTIVE),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email="new.member@example.com")
        self.assertEqual(user.role, User.Role.MEMBER)
        self.assertEqual(user.status, User.Status.PENDING)

    def test_rejects_mismatched_passwords(self):
        response = self.client.post(
            self.url,
            self.payload(password_confirmation="Something-else-1!"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password_confirmation", response.json())
        self.assertFalse(User.objects.exists())

    def test_rejects_weak_password(self):
        response = self.client.post(
            self.url,
            self.payload(password="12345678", password_confirmation="12345678"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.json())

    def test_rejects_duplicate_email(self):
        make_user(email="new.member@example.com")

        response = self.client.post(self.url, self.payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.json())


# ============================================================================
# LOGIN, TOKEN REFRESH, LOGOUT
# ============================================================================

class LoginTests(AuthTestCase):
    def test_active_user_receives_tokens(self):
        user = make_user()

        response = self.login(user.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["code"], "S002")
        self.assertIn("access", body["data"])
        self.assertIn("refresh", body["data"])
        self.assertEqual(body["data"]["user"]["email"], user.email)

    def test_email_is_case_insensitive(self):
        make_user(email="member@example.com")

        response = self.login("Member@Example.COM")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_wrong_password(self):
        user = make_user()

        response = self.login(user.email, "wrong-password")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.json()["code"], "E001")

    def test_unknown_email(self):
        response = self.login("nobody@example.com")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.json()["code"], "E001")

    def test_blocked_statuses(self):
        cases = [
            (User.Status.PENDING, "E002"),
            (User.Status.REJECTED, "E003"),
            (User.Status.SUSPENDED, "E004"),
        ]

        for user_status, code in cases:
            with self.subTest(status=user_status):
                user = make_user(
                    email=f"{user_status.lower()}@example.com",
                    status=user_status,
                    is_active=False,
                )

                response = self.login(user.email)

                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(response.json()["code"], code)

    def test_inactive_user(self):
        user = make_user(is_active=False)

        response = self.login(user.email)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.json()["code"], "E005")


class TokenRefreshTests(AuthTestCase):
    def test_returns_new_token_pair(self):
        user = make_user()
        tokens = self.tokens_for(user.email)

        response = self.refresh(tokens["refresh"])

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.json())
        self.assertIn("refresh", response.json())

    def test_refresh_token_works_only_once(self):
        user = make_user()
        tokens = self.tokens_for(user.email)
        self.refresh(tokens["refresh"])

        response = self.refresh(tokens["refresh"])

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_rejects_garbage_token(self):
        response = self.refresh("not-a-token")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class LogoutTests(AuthTestCase):
    url = reverse("accounts:logout")

    def test_blacklists_refresh_token(self):
        user = make_user()
        tokens = self.authenticate(user)

        response = self.client.post(
            self.url,
            {"refresh": tokens["refresh"]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["code"], "S003")
        self.assertEqual(
            self.refresh(tokens["refresh"]).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_requires_authentication(self):
        user = make_user()
        tokens = self.tokens_for(user.email)

        response = self.client.post(
            self.url,
            {"refresh": tokens["refresh"]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_cannot_revoke_another_users_token(self):
        victim = make_user(email="victim@example.com")
        victim_tokens = self.tokens_for(victim.email)
        self.authenticate(make_user(email="attacker@example.com"))

        response = self.client.post(
            self.url,
            {"refresh": victim_tokens["refresh"]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["code"], "E007")
        self.assertEqual(
            self.refresh(victim_tokens["refresh"]).status_code,
            status.HTTP_200_OK,
        )

    def test_rejects_invalid_token(self):
        self.authenticate(make_user())

        response = self.client.post(
            self.url,
            {"refresh": "not-a-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["code"], "E007")


# ============================================================================
# CURRENT USER
# ============================================================================

class MeTests(AuthTestCase):
    url = reverse("accounts:me")

    def test_requires_authentication(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_returns_current_user(self):
        user = make_user(first_name="Jane")
        self.authenticate(user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["data"]["email"], user.email)
        self.assertEqual(response.json()["data"]["first_name"], "Jane")

    def test_updates_name_but_not_protected_fields(self):
        user = make_user()
        self.authenticate(user)

        response = self.client.patch(
            self.url,
            {
                "first_name": "Janet",
                "email": "changed@example.com",
                "role": User.Role.SUPER_ADMIN,
                "status": User.Status.SUSPENDED,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Janet")
        self.assertEqual(user.email, "member@example.com")
        self.assertEqual(user.role, User.Role.MEMBER)
        self.assertEqual(user.status, User.Status.ACTIVE)


class MyProfileTests(AuthTestCase):
    url = reverse("accounts:my-profile")

    def test_creates_profile_on_first_read(self):
        user = make_user()
        self.authenticate(user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["data"]["phone_number"], "")
        self.assertTrue(MemberProfile.objects.filter(user=user).exists())

    def test_updates_phone_number(self):
        user = make_user()
        self.authenticate(user)

        response = self.client.patch(
            self.url,
            {"phone_number": "+254700000000"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(user.member_profile.phone_number, "+254700000000")


# ============================================================================
# PASSWORD CHANGE
# ============================================================================

class ChangePasswordTests(AuthTestCase):
    url = reverse("accounts:change-password")

    def change(self, current=PASSWORD, new=NEW_PASSWORD, confirmation=None):
        return self.client.post(
            self.url,
            {
                "current_password": current,
                "new_password": new,
                "new_password_confirmation": confirmation or new,
            },
            format="json",
        )

    def test_changes_password(self):
        user = make_user()
        self.authenticate(user)

        response = self.change()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["code"], "S006")
        user.refresh_from_db()
        self.assertTrue(user.check_password(NEW_PASSWORD))

    def test_signs_out_other_devices_and_returns_new_tokens(self):
        user = make_user()
        other_device = self.tokens_for(user.email)
        this_device = self.authenticate(user)

        response = self.change()

        new_tokens = response.json()["data"]
        self.assertEqual(
            self.refresh(other_device["refresh"]).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )
        self.assertEqual(
            self.refresh(this_device["refresh"]).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )
        self.assertEqual(
            self.refresh(new_tokens["refresh"]).status_code,
            status.HTTP_200_OK,
        )

    def test_rejects_wrong_current_password(self):
        user = make_user()
        self.authenticate(user)

        response = self.change(current="wrong-password")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("current_password", response.json())
        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))

    def test_rejects_mismatched_confirmation(self):
        self.authenticate(make_user())

        response = self.change(confirmation="Something-else-1!")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("new_password_confirmation", response.json())

    def test_requires_authentication(self):
        response = self.change()

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# ============================================================================
# PASSWORD RESET
# ============================================================================

class PasswordResetTests(AuthTestCase):
    request_url = reverse("accounts:password-reset")
    confirm_url = reverse("accounts:password-reset-confirm")

    def request_reset(self, email):
        return self.client.post(
            self.request_url,
            {"email": email},
            format="json",
        )

    def reset_link_params(self):
        self.assertEqual(len(mail.outbox), 1)
        match = re.search(r"uid=([^&\s]+)&token=(\S+)", mail.outbox[0].body)
        return match.group(1), match.group(2)

    def confirm(self, uid, token, new=NEW_PASSWORD):
        return self.client.post(
            self.confirm_url,
            {
                "uid": uid,
                "token": token,
                "new_password": new,
                "new_password_confirmation": new,
            },
            format="json",
        )

    def test_sends_link_to_frontend(self):
        user = make_user()

        response = self.request_reset("Member@Example.com")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["code"], "S011")
        self.assertEqual(mail.outbox[0].to, [user.email])
        self.assertIn(
            "http://localhost:3000/reset-password?uid=",
            mail.outbox[0].body,
        )

    def test_unknown_email_gets_same_response_and_no_email(self):
        response = self.request_reset("nobody@example.com")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["code"], "S011")
        self.assertEqual(len(mail.outbox), 0)

    def test_inactive_user_gets_no_email(self):
        make_user(status=User.Status.PENDING, is_active=False)

        self.request_reset("member@example.com")

        self.assertEqual(len(mail.outbox), 0)

    def test_confirm_sets_password_and_signs_out_everywhere(self):
        user = make_user()
        old_tokens = self.tokens_for(user.email)
        self.request_reset(user.email)
        uid, token = self.reset_link_params()

        response = self.confirm(uid, token)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["code"], "S012")
        user.refresh_from_db()
        self.assertTrue(user.check_password(NEW_PASSWORD))
        self.assertEqual(
            self.refresh(old_tokens["refresh"]).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_link_works_only_once(self):
        user = make_user()
        self.request_reset(user.email)
        uid, token = self.reset_link_params()
        self.confirm(uid, token)

        response = self.confirm(uid, token, new="Another-pass-789!")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("token", response.json())

    def test_rejects_bad_token_and_uid(self):
        user = make_user()
        self.request_reset(user.email)
        uid, _ = self.reset_link_params()

        for bad_uid, bad_token in [(uid, "bad-token"), ("bad-uid", "bad-token")]:
            with self.subTest(uid=bad_uid, token=bad_token):
                response = self.confirm(bad_uid, bad_token)

                self.assertEqual(
                    response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )
                self.assertIn("token", response.json())

        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))


# ============================================================================
# MEMBER MANAGEMENT
# ============================================================================

class MemberManagementTests(AuthTestCase):
    def setUp(self):
        self.admin = make_user(email="admin@example.com", role=User.Role.ADMIN)
        self.manager = make_user(
            email="manager@example.com",
            role=User.Role.MANAGER,
        )
        self.finance = make_user(
            email="finance@example.com",
            role=User.Role.FINANCE_OFFICER,
        )
        self.member = make_user(email="member@example.com")
        self.pending = make_user(
            email="pending@example.com",
            status=User.Status.PENDING,
            is_active=False,
        )

    def action_url(self, name, user):
        return reverse(f"accounts:user-{name}", args=[user.pk])

    def test_list_is_staff_only(self):
        for user, expected in [
            (self.finance, status.HTTP_200_OK),
            (self.member, status.HTTP_403_FORBIDDEN),
        ]:
            with self.subTest(role=user.role):
                self.authenticate(user)

                response = self.client.get(reverse("accounts:user-list"))

                self.assertEqual(response.status_code, expected)

    def test_staff_cannot_create_or_delete_users(self):
        self.authenticate(self.admin)

        create = self.client.post(
            reverse("accounts:user-list"),
            {"email": "x@example.com"},
            format="json",
        )
        delete = self.client.delete(
            reverse("accounts:user-detail", args=[self.member.pk])
        )

        self.assertEqual(create.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(delete.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_only_admins_can_edit_users(self):
        url = reverse("accounts:user-detail", args=[self.member.pk])

        self.authenticate(self.manager)
        response = self.client.patch(url, {"first_name": "X"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.authenticate(self.admin)
        response = self.client.patch(url, {"first_name": "X"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_approve_pending_member(self):
        self.authenticate(self.manager)

        response = self.client.post(self.action_url("approve", self.pending))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["code"], "S007")
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, User.Status.ACTIVE)
        self.assertTrue(self.pending.is_active)
        self.assertIsNotNone(self.pending.approved_at)
        self.assertTrue(
            MemberProfile.objects.filter(user=self.pending).exists()
        )

    def test_reject_pending_member(self):
        self.authenticate(self.admin)

        response = self.client.post(self.action_url("reject", self.pending))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["code"], "S008")
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, User.Status.REJECTED)

    def test_suspend_and_reactivate_member(self):
        self.authenticate(self.admin)

        response = self.client.post(self.action_url("suspend", self.member))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["code"], "S009")
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, User.Status.SUSPENDED)
        self.assertFalse(self.member.is_active)

        response = self.client.post(self.action_url("reactivate", self.member))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["code"], "S010")
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, User.Status.ACTIVE)
        self.assertTrue(self.member.is_active)

    def test_invalid_status_transition(self):
        self.authenticate(self.admin)

        response = self.client.post(self.action_url("approve", self.member))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["code"], "E006")

    def test_status_actions_need_admin_or_manager(self):
        self.authenticate(self.finance)

        response = self.client.post(self.action_url("approve", self.pending))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, User.Status.PENDING)


class MemberProfileManagementTests(AuthTestCase):
    def setUp(self):
        self.member = make_user()
        self.profile = MemberProfile.objects.create(user=self.member)
        self.url = reverse("accounts:profile-detail", args=[self.profile.pk])

    def test_staff_can_read_profiles(self):
        self.authenticate(
            make_user(email="finance@example.com", role=User.Role.FINANCE_OFFICER)
        )

        response = self.client.get(reverse("accounts:profile-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_members_cannot_read_profiles(self):
        self.authenticate(self.member)

        response = self.client.get(reverse("accounts:profile-list"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_only_admins_can_edit_profiles(self):
        self.authenticate(
            make_user(email="manager@example.com", role=User.Role.MANAGER)
        )
        response = self.client.patch(
            self.url,
            {"phone_number": "+254700000000"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.authenticate(
            make_user(email="admin@example.com", role=User.Role.ADMIN)
        )
        response = self.client.patch(
            self.url,
            {"phone_number": "+254700000000"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.phone_number, "+254700000000")
