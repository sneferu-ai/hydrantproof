"""Ledger app configuration."""
from django.apps import AppConfig


class LedgerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ledger"
    verbose_name = "HydrantProof ledger"

    def ready(self):  # pragma: no cover - import side effect
        from . import schema  # noqa: F401  (registers OpenAPI auth extensions)
        from . import signals  # noqa: F401  (registers post_save receivers)
