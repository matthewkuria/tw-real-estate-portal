from decimal import Decimal

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.estates.models import Estate
from apps.notifications.models import Notification
from apps.notifications.types import NotificationType
from apps.payments.models import Payment
from apps.rentals.models import RentalHouse

from . import services
from .models import PlotAllocation, Property


def make_user(email, role=User.Role.MEMBER, status=User.Status.ACTIVE, **extra):
    return User.objects.create_user(
        email=email,
        password="Pass-word-123!",
        role=role,
        status=status,
        is_active=True,
        **extra,
    )


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PlotTestCase(APITestCase):
    list_url = reverse("properties:plot-list")

    def setUp(self):
        self.admin = make_user("admin@example.com", User.Role.ADMIN)
        self.manager = make_user("manager@example.com", User.Role.MANAGER)
        self.finance = make_user("finance@example.com", User.Role.FINANCE_OFFICER)
        self.member = make_user("jane@example.com", first_name="Jane", last_name="Wanjiru")
        self.other = make_user("otis@example.com")

        self.oak = Estate.objects.create(name="Oak", code="oak", location="Nairobi")
        self.palm = Estate.objects.create(name="Palm", code="palm", location="Mombasa")

        self.plot = Property.objects.create(
            estate=self.oak,
            plot_number="14",
            price=Decimal("1000000"),
            required_deposit=Decimal("200000"),
        )

        self.client.force_authenticate(self.admin)

    def pay(self, amount, member=None, estate=None, plot=None, status=Payment.Status.CONFIRMED):
        return Payment.objects.create(
            member=member or self.member,
            estate=estate or self.oak,
            property=plot,
            amount=Decimal(amount),
            method=Payment.Method.MPESA,
            status=status,
            recorded_by=self.finance,
            confirmed_by=self.finance if status == Payment.Status.CONFIRMED else None,
        )

    def url(self, name, plot=None):
        return reverse(f"properties:plot-{name}", args=[(plot or self.plot).pk])

    def allocate(self, member=None, plot=None, **extra):
        return self.client.post(
            self.url("allocate", plot),
            {"member": (member or self.member).pk, **extra},
            format="json",
        )


class AllocateTests(PlotTestCase):
    def test_allocates_once_deposit_is_paid(self):
        self.pay("150000")
        self.pay("50000")
        pending = self.pay("300000", status=Payment.Status.PENDING)

        response = self.allocate(notes="Ballot winner")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["code"], "S098")
        data = response.data["data"]
        self.assertEqual(data["status"], "ALLOCATED")
        self.assertEqual(data["member"]["id"], self.member.pk)
        self.assertEqual(Decimal(data["amount_paid"]), Decimal("200000"))
        self.assertEqual(Decimal(data["balance"]), Decimal("800000"))

        self.plot.refresh_from_db()
        self.assertIsNotNone(self.plot.allocated_at)

        record = PlotAllocation.objects.get()
        self.assertEqual(record.member, self.member)
        self.assertEqual(record.allocated_by, self.admin)
        self.assertEqual(record.deposit_paid, Decimal("200000"))
        self.assertFalse(record.deposit_waived)
        self.assertEqual(record.notes, "Ballot winner")

        # The member's unassigned payments toward the estate now sit on the plot.
        self.assertEqual(Payment.objects.filter(property=self.plot).count(), 3)
        pending.refresh_from_db()
        self.assertEqual(pending.property, self.plot)

        notification = Notification.objects.get(
            recipient=self.member, type=NotificationType.PLOT_ALLOCATED,
        )
        self.assertEqual(notification.data, {"property": self.plot.pk})

    def test_deposit_shortfall_is_refused_with_amounts(self):
        self.pay("50000")
        self.pay("500000", status=Payment.Status.PENDING)
        self.pay("500000", estate=self.palm)

        response = self.allocate()

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "E022")
        self.assertEqual(
            response.data["data"],
            {"required_deposit": "200000.00", "deposit_paid": "50000.00", "shortfall": "150000.00"},
        )
        self.plot.refresh_from_db()
        self.assertEqual(self.plot.status, Property.Status.AVAILABLE)
        self.assertFalse(PlotAllocation.objects.exists())

    def test_payments_on_another_plot_dont_count(self):
        other_plot = Property.objects.create(
            estate=self.oak, plot_number="15", price=1000000, required_deposit=200000,
        )
        self.pay("200000", plot=other_plot)

        self.assertEqual(self.allocate().data["code"], "E022")

    def test_deposit_can_be_waived_with_a_reason(self):
        no_reason = self.allocate(waive_deposit=True)
        with_reason = self.allocate(waive_deposit=True, notes="Board approved instalments")

        self.assertEqual(no_reason.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(with_reason.status_code, status.HTTP_200_OK)
        self.assertTrue(PlotAllocation.objects.get().deposit_waived)

    def test_waiver_isnt_recorded_when_deposit_is_paid(self):
        self.pay("200000")

        self.allocate(waive_deposit=True, notes="Not needed")

        self.assertFalse(PlotAllocation.objects.get().deposit_waived)

    def test_allocated_plot_must_be_released_first(self):
        self.pay("200000")
        self.pay("200000", member=self.other)
        self.allocate()

        response = self.allocate(member=self.other)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "E020")
        self.plot.refresh_from_db()
        self.assertEqual(self.plot.allocated_to, self.member)

    def test_only_active_members_can_hold_plots(self):
        suspended = make_user("sus@example.com", status=User.Status.SUSPENDED)
        pending = make_user("new@example.com", status=User.Status.PENDING)

        for user in (suspended, pending, self.manager):
            response = self.allocate(member=user, waive_deposit=True, notes="x")
            self.assertEqual(response.data["code"], "E021", user.email)

    def test_only_admins_and_managers_allocate_and_release(self):
        self.pay("200000")

        for user in (self.finance, self.member):
            self.client.force_authenticate(user)
            self.assertEqual(self.allocate().status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(
                self.client.post(self.url("release"), {"reason": "x"}).status_code,
                status.HTTP_403_FORBIDDEN,
            )

        super_admin = make_user("root@example.com", User.Role.SUPER_ADMIN)
        self.client.force_authenticate(super_admin)
        self.assertEqual(self.allocate().status_code, status.HTTP_200_OK)

    def test_manager_allocates_and_releases(self):
        self.pay("200000")
        self.client.force_authenticate(self.manager)

        response = self.allocate()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(PlotAllocation.objects.get().allocated_by, self.manager)

        response = self.client.post(self.url("release"), {"reason": "Withdrew"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(PlotAllocation.objects.get().released_by, self.manager)

        # Allocating doesn't let managers edit or add plots.
        self.assertEqual(
            self.client.post(
                self.list_url,
                {"estate": self.oak.pk, "plot_number": "99", "price": "10", "required_deposit": "5"},
                format="json",
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )


class ExistingOwnerTests(PlotTestCase):
    """
    Plots owned before the portal have no price or deposit, and are
    allocated without any payment.
    """

    def test_plot_can_be_added_without_price_or_deposit(self):
        response = self.client.post(
            self.list_url, {"estate": self.oak.pk, "plot_number": "40"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data["data"]["price"])
        self.assertIsNone(response.data["data"]["required_deposit"])

    def test_allocates_without_payment_when_no_deposit_is_set(self):
        plot = Property.objects.create(estate=self.oak, plot_number="41")

        response = self.allocate(plot=plot)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["data"]["balance"])
        record = PlotAllocation.objects.get()
        self.assertFalse(record.deposit_waived)
        self.assertEqual(record.deposit_paid, Decimal("0"))

    def test_deposit_still_checked_when_set(self):
        self.assertEqual(self.allocate().status_code, status.HTTP_409_CONFLICT)

    def test_deposit_still_cant_exceed_a_price(self):
        response = self.client.post(
            self.list_url,
            {"estate": self.oak.pk, "plot_number": "42", "price": "100", "required_deposit": "200"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ReleaseTests(PlotTestCase):
    def setUp(self):
        super().setUp()
        self.payment = self.pay("200000")
        self.allocate()

    def test_release_frees_plot_and_closes_history(self):
        response = self.client.post(self.url("release"), {"reason": "Member withdrew"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["code"], "S099")
        data = response.data["data"]
        self.assertEqual(data["status"], "AVAILABLE")
        self.assertIsNone(data["member"])
        self.assertIsNone(data["amount_paid"])

        record = PlotAllocation.objects.get()
        self.assertIsNotNone(record.released_at)
        self.assertEqual(record.released_by, self.admin)
        self.assertEqual(record.release_reason, "Member withdrew")

        # Payments stay on record against the plot.
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.property, self.plot)

        self.assertTrue(
            Notification.objects.filter(
                recipient=self.member, type=NotificationType.PLOT_RELEASED,
            ).exists()
        )

    def test_reason_is_required(self):
        response = self.client.post(self.url("release"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("reason", response.data)

    def test_releasing_an_available_plot_is_refused(self):
        self.client.post(self.url("release"), {"reason": "x"}, format="json")

        response = self.client.post(self.url("release"), {"reason": "again"}, format="json")

        self.assertEqual(response.data["code"], "E023")

    def test_reallocating_starts_a_new_record(self):
        self.client.post(self.url("release"), {"reason": "Withdrew"}, format="json")
        self.pay("200000", member=self.other)

        response = self.allocate(member=self.other)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # The old holder's payments don't count toward the new holder.
        self.assertEqual(Decimal(response.data["data"]["amount_paid"]), Decimal("200000"))

        history = self.client.get(self.url("allocations")).data["data"]
        self.assertEqual([h["member"]["id"] for h in history], [self.other.pk, self.member.pk])
        self.assertEqual([h["is_current"] for h in history], [True, False])

    def test_history_is_staff_only(self):
        self.client.force_authenticate(self.finance)
        self.assertEqual(self.client.get(self.url("allocations")).status_code, status.HTTP_200_OK)

        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.get(self.url("allocations")).status_code, status.HTTP_403_FORBIDDEN)


class BrowsePlotTests(PlotTestCase):
    def setUp(self):
        super().setUp()
        self.pay("200000")
        self.allocate()
        self.free = Property.objects.create(
            estate=self.palm, plot_number="3", price=500000, required_deposit=50000,
        )

    def numbers(self, response):
        return [item["plot_number"] for item in response.data["data"]["results"]]

    def test_staff_see_and_filter_every_plot(self):
        self.client.force_authenticate(self.finance)

        self.assertEqual(self.numbers(self.client.get(self.list_url)), ["14", "3"])
        self.assertEqual(self.numbers(self.client.get(self.list_url, {"status": "AVAILABLE"})), ["3"])
        self.assertEqual(self.numbers(self.client.get(self.list_url, {"estate": self.palm.pk})), ["3"])
        self.assertEqual(self.numbers(self.client.get(self.list_url, {"member": self.member.pk})), ["14"])
        self.assertEqual(self.numbers(self.client.get(self.list_url, {"search": "wanjiru"})), ["14"])

    def test_members_see_only_their_plots(self):
        self.client.force_authenticate(self.member)
        mine = self.client.get(self.list_url)

        self.assertEqual(self.numbers(mine), ["14"])
        self.assertEqual(Decimal(mine.data["data"]["results"][0]["balance"]), Decimal("800000"))
        self.assertEqual(
            self.client.get(self.url("detail", self.free)).status_code,
            status.HTTP_404_NOT_FOUND,
        )

        self.client.force_authenticate(self.other)
        self.assertEqual(self.numbers(self.client.get(self.list_url)), [])

    def test_bad_filters_are_rejected(self):
        for params in ({"status": "SOLD"}, {"estate": "oak"}):
            self.assertEqual(
                self.client.get(self.list_url, params).status_code,
                status.HTTP_400_BAD_REQUEST,
            )


class ManagePlotTests(PlotTestCase):
    def test_create_and_edit(self):
        response = self.client.post(
            self.list_url,
            {"estate": self.oak.pk, "plot_number": " 15 ", "price": "900000", "required_deposit": "90000"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"]["plot_number"], "15")
        self.assertEqual(response.data["data"]["status"], "AVAILABLE")

        plot_url = self.url("detail", Property.objects.get(plot_number="15"))
        edit = self.client.patch(plot_url, {"price": "950000", "status": "ALLOCATED"}, format="json")

        self.assertEqual(edit.status_code, status.HTTP_200_OK)
        self.assertEqual(edit.data["data"]["price"], "950000.00")
        # Allocation can't be set by editing.
        self.assertEqual(edit.data["data"]["status"], "AVAILABLE")

    def test_validation(self):
        base = {"estate": self.oak.pk, "price": "100", "required_deposit": "10"}

        duplicate = self.client.post(self.list_url, {**base, "plot_number": "14"}, format="json")
        deposit = self.client.post(
            self.list_url,
            {**base, "plot_number": "16", "required_deposit": "200"},
            format="json",
        )
        move = self.client.patch(self.url("detail"), {"estate": self.palm.pk}, format="json")

        self.assertIn("plot_number", duplicate.data)
        self.assertIn("required_deposit", deposit.data)
        self.assertIn("estate", move.data)

    def test_delete_only_unused_plots(self):
        self.assertEqual(self.client.delete(self.url("detail")).status_code, status.HTTP_200_OK)

        used = Property.objects.create(estate=self.oak, plot_number="20", price=10, required_deposit=1)
        RentalHouse.objects.create(property=used, house_number="A1", house_type="BUNGALOW")

        response = self.client.delete(self.url("detail", used))

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "E024")

    def test_staff_other_than_admins_cant_change_plots(self):
        self.client.force_authenticate(self.manager)

        self.assertEqual(
            self.client.patch(self.url("detail"), {"price": "1"}, format="json").status_code,
            status.HTTP_403_FORBIDDEN,
        )


class ConsistencyTests(TestCase):
    def test_status_and_holder_must_agree(self):
        estate = Estate.objects.create(name="Oak", code="oak", location="Nairobi")
        member = make_user("m@example.com")

        for fields in (
            {"status": Property.Status.AVAILABLE, "allocated_to": member},
            {"status": Property.Status.ALLOCATED},
        ):
            with self.subTest(fields=fields), self.assertRaises(IntegrityError), transaction.atomic():
                Property.objects.create(
                    estate=estate, plot_number="1", price=10, required_deposit=1, **fields,
                )


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AdminAllocationTests(PlotTestCase):
    def setUp(self):
        super().setUp()
        self.admin.is_staff = self.admin.is_superuser = True
        self.admin.save()
        self.client.force_login(self.admin)
        self.change_url = reverse("admin:properties_property_change", args=[self.plot.pk])

    def post(self, **extra):
        return self.client.post(
            self.change_url,
            {
                "estate": self.oak.pk,
                "plot_number": "14",
                "description": "",
                "price": "1000000",
                "required_deposit": "200000",
                "allocations-TOTAL_FORMS": "0",
                "allocations-INITIAL_FORMS": "0",
                **extra,
            },
        )

    def test_admin_runs_the_same_checks(self):
        refused = self.post(allocate_to=self.member.pk)
        self.assertEqual(refused.status_code, 200)
        self.assertContains(refused, "required deposit")

        self.pay("200000")
        self.assertEqual(self.post(allocate_to=self.member.pk).status_code, 302)

        self.plot.refresh_from_db()
        self.assertEqual(self.plot.allocated_to, self.member)
        self.assertEqual(PlotAllocation.objects.get().allocated_by, self.admin)

        self.assertEqual(self.post(release_reason="Withdrew").status_code, 302)
        self.plot.refresh_from_db()
        self.assertEqual(self.plot.status, Property.Status.AVAILABLE)


class BackfillMigrationTests(TransactionTestCase):
    """
    Plots edited by hand before the workflow existed are made consistent
    and their holders recorded.
    """

    def targets(self, executor, properties_migration):
        """
        Every app at its latest migration except properties.
        """
        return [
            node for node in executor.loader.graph.leaf_nodes() if node[0] != "properties"
        ] + [("properties", properties_migration)]

    def setUp(self):
        executor = MigrationExecutor(connection)
        before = self.targets(executor, "0002_plot_allocation")
        executor.migrate(before)
        old_apps = executor.loader.project_state(before).apps

        Estate = old_apps.get_model("estates", "Estate")
        OldProperty = old_apps.get_model("properties", "Property")
        OldUser = old_apps.get_model("accounts", "User")

        estate = Estate.objects.create(name="Oak", code="oak", location="Nairobi")
        member = OldUser.objects.create(email="m@example.com", role="MEMBER", status="ACTIVE")

        def plot(number, **fields):
            return OldProperty.objects.create(
                estate=estate, plot_number=number, price=10, required_deposit=1, **fields,
            )

        self.held_but_available = plot("1", allocated_to=member, status="AVAILABLE").pk
        self.allocated_to_nobody = plot("2", status="ALLOCATED").pk
        self.member_pk = member.pk

        executor = MigrationExecutor(connection)
        executor.migrate(self.targets(executor, "0004_property_allocation_consistent"))

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_backfill(self):
        held = Property.objects.get(pk=self.held_but_available)
        nobody = Property.objects.get(pk=self.allocated_to_nobody)

        self.assertEqual(held.status, Property.Status.ALLOCATED)
        self.assertIsNotNone(held.allocated_at)
        self.assertEqual(nobody.status, Property.Status.AVAILABLE)

        record = PlotAllocation.objects.get()
        self.assertEqual((record.property_id, record.member_id), (held.pk, self.member_pk))
        self.assertIsNone(record.released_at)

        # And a release closes that backfilled record rather than adding one.
        services.release(held, by=None, reason="Tidy up")
        self.assertEqual(PlotAllocation.objects.get().release_reason, "Tidy up")
