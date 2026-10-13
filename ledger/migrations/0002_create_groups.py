"""Create the three role groups, backfill UserProfile, seed the DistrictSettings singleton."""
from django.db import migrations

GROUPS = ("admin", "editor", "viewer")


def create_groups_and_defaults(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")
    UserProfile = apps.get_model("ledger", "UserProfile")
    DistrictSettings = apps.get_model("ledger", "DistrictSettings")

    for name in GROUPS:
        Group.objects.get_or_create(name=name)

    for user in User.objects.all():
        UserProfile.objects.get_or_create(user=user, defaults={"org_label": "District"})

    DistrictSettings.objects.get_or_create(pk=1)


def remove_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=GROUPS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(create_groups_and_defaults, remove_groups),
    ]
