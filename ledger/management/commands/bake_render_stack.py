"""Build-time: freeze the render stack facts the pack manifest cites."""
from django.core.management.base import BaseCommand

from ledger.render_stack import build_payload, write_render_stack


class Command(BaseCommand):
    help = "Write the frozen render-stack facts (base image digest, python, requirements, freeze)."

    def add_arguments(self, parser):
        parser.add_argument("--output", default="/app/render_stack.json")

    def handle(self, *args, **options):
        path = write_render_stack(options["output"])
        payload = build_payload()
        self.stdout.write(f"render stack written to {path}")
        for key, value in payload.items():
            self.stdout.write(f"  {key}: {value}")
