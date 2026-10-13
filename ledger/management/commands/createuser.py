"""Create a district account (admins use this or the Django admin)."""
from __future__ import annotations

import getpass

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.password_validation import validate_password

from ledger.models import UserProfile


class Command(BaseCommand):
    help = "Create a district user with a role group (admin/editor/viewer)."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--role", default="viewer", choices=("admin", "editor", "viewer"))
        parser.add_argument("--password", default=None)
        parser.add_argument("--org-label", default="District")

    def handle(self, *args, **options):
        username = options["username"]
        if User.objects.filter(username=username).exists():
            raise CommandError(f"user {username} already exists")
        password = options["password"] or getpass.getpass("Password: ")
        validate_password(password)
        user = User.objects.create_user(username=username, password=password)
        group, _ = Group.objects.get_or_create(name=options["role"])
        user.groups.add(group)
        UserProfile.objects.update_or_create(user=user, defaults={"org_label": options["org_label"]})
        self.stdout.write(self.style.SUCCESS(f"created {username} with role {options['role']}"))
