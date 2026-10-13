"""Tier A truth tables: database-enforced append-only (BEFORE UPDATE OR DELETE).

Eight tables exactly (specification §5).  A raw psql UPDATE or DELETE against
any of them raises; the sanctioned lifecycle commands (``purge_demo``,
``import_ledger``) disable these triggers inside their own transaction.
"""
from django.db import migrations

TIER_A_TABLES = (
    "ledger_flowtest",
    "ledger_outletreading",
    "ledger_hydrantstatusevent",
    "ledger_hydrantattributechange",
    "ledger_parcelattributechange",
    "ledger_inspection",
    "ledger_oosacknowledgement",
    "ledger_settingsaudit",
)

CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION ledger_append_only_guard() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ledger table % is append-only (Tier A truth); % is refused',
        TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""

DROP_FUNCTION = "DROP FUNCTION IF EXISTS ledger_append_only_guard();"


def install_triggers(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(CREATE_FUNCTION)
        for table in TIER_A_TABLES:
            cursor.execute(
                f"CREATE TRIGGER {table}_append_only "
                f"BEFORE UPDATE OR DELETE ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION ledger_append_only_guard();"
            )


def drop_triggers(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for table in TIER_A_TABLES:
            cursor.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table};")
        cursor.execute(DROP_FUNCTION)


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0002_create_groups"),
    ]

    operations = [
        migrations.RunPython(install_triggers, drop_triggers),
    ]
