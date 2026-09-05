"""Command to extract Cambridge curriculum frameworks from source syllabus files into canonical JSON fixtures."""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.curriculum.services.extractor import CurriculumExtractor


class Command(BaseCommand):
    help = "Extracts Cambridge syllabus files into structured JSON seed fixtures."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            type=str,
            default=r"F:\OBJECTIVES",
            help="Path to source objectives directory (default: F:\\OBJECTIVES)",
        )
        parser.add_argument(
            "--output-dir",
            type=str,
            default=str(Path(settings.BASE_DIR) / "apps" / "curriculum" / "data"),
            help="Output directory for JSON seed files",
        )

    def handle(self, *args, **options):
        source_dir = options["source"]
        output_dir = Path(options["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        self.stdout.write(self.style.MIGRATE_HEADING(f"Extracting curriculum from {source_dir}..."))

        extractor = CurriculumExtractor(source_dir)
        frameworks_data = extractor.extract_all()

        total_los = 0
        total_schemes = 0

        for fw_name, fw_data in frameworks_data.items():
            slug = fw_name.lower().replace(" ", "_")
            out_file = output_dir / f"{slug}.json"

            fw_schemes = fw_data.get("schemes", [])
            fw_los = sum(
                len(lo)
                for s in fw_schemes
                for t in s.get("topics", [])
                for lo in [t.get("los", [])]
            )
            total_schemes += len(fw_schemes)
            total_los += fw_los

            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(fw_data, f, indent=2, ensure_ascii=False)

            self.stdout.write(
                self.style.SUCCESS(
                    f"  Saved {fw_name}: {len(fw_schemes)} schemes, {fw_los} learning objectives -> {out_file.name}"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nExtraction complete! Total: {total_schemes} schemes, {total_los} learning objectives saved to {output_dir}"
            )
        )
