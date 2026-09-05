"""Management command to load/migrate Cambridge curriculum data from JSON fixtures into the database."""

import json
import time
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, connection, transaction

from apps.curriculum.models import (
    CurriculumFramework,
    LearningObjective,
    SchemeOfWork,
    Subtopic,
    Topic,
)


class Command(BaseCommand):
    help = "Loads Cambridge curriculum frameworks, schemes, topics, and learning objectives from JSON seed files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--framework",
            type=str,
            help="Filter by framework name (e.g. 'Cambridge Primary', 'Cambridge Lower Secondary', 'Cambridge IGCSE')",
        )
        parser.add_argument(
            "--data-dir",
            type=str,
            default=str(Path(settings.BASE_DIR) / "apps" / "curriculum" / "data"),
            help="Directory containing JSON seed fixtures",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the seed process without committing changes to the database",
        )

    def handle(self, *args, **options):
        data_dir = Path(options["data_dir"])
        dry_run = options["dry_run"]
        framework_filter = options.get("framework")

        if not data_dir.exists():
            raise CommandError(f"Data directory not found: {data_dir}")

        json_files = sorted(data_dir.glob("*.json"))
        if not json_files:
            raise CommandError(f"No JSON seed files found in: {data_dir}")

        self.stdout.write(self.style.MIGRATE_HEADING("Starting Cambridge Curriculum Seed..."))

        total_frameworks = 0
        total_schemes = 0
        total_topics = 0
        total_subtopics = 0
        total_los = 0

        for json_file in json_files:
            with open(json_file, "r", encoding="utf-8") as f:
                fw_data = json.load(f)

            fw_name = fw_data.get("name", "")
            if framework_filter and framework_filter.lower() not in fw_name.lower():
                continue

            fw_code = fw_data.get("code", fw_name.upper().replace(" ", "_"))
            publisher = fw_data.get("publisher", "Cambridge International")

            self.stdout.write(self.style.MIGRATE_LABEL(f"\nProcessing framework: {fw_name} ({fw_code})"))

            if not dry_run:
                if not connection.in_atomic_block:
                    close_old_connections()
                framework, _ = CurriculumFramework.objects.get_or_create(
                    code=fw_code,
                    defaults={"name": fw_name, "publisher": publisher, "is_active": True},
                )
                if framework.name != fw_name or framework.publisher != publisher:
                    framework.name = fw_name
                    framework.publisher = publisher
                    framework.save()
            else:
                framework = CurriculumFramework(code=fw_code, name=fw_name, publisher=publisher)

            total_frameworks += 1
            schemes_data = fw_data.get("schemes", [])

            for s_data in schemes_data:
                sub_code = s_data.get("subject_code", "")
                sub_name = s_data.get("subject_name", "")
                year_group = s_data.get("year_group", "")
                title = s_data.get("title", f"{sub_name} {year_group}")
                topics_data = s_data.get("topics", [])

                if dry_run:
                    lo_count = sum(len(t.get("los", [])) for t in topics_data)
                    self.stdout.write(
                        f"  [DRY RUN] {sub_code} {sub_name} ({year_group}): {len(topics_data)} topics, {lo_count} LOs"
                    )
                    total_schemes += 1
                    total_topics += len(topics_data)
                    total_los += lo_count
                    continue

                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        if not connection.in_atomic_block:
                            close_old_connections()
                        with transaction.atomic():
                            scheme, created = SchemeOfWork.objects.get_or_create(
                                framework=framework,
                                subject_code=sub_code,
                                year_group=year_group,
                                version=1,
                                defaults={
                                    "subject_name": sub_name,
                                    "title": title,
                                    "is_active": True,
                                    "published_on": date.today(),
                                },
                            )

                            if not created and (scheme.subject_name != sub_name or scheme.title != title):
                                scheme.subject_name = sub_name
                                scheme.title = title
                                scheme.save()

                            existing_topics = {t.sequence: t for t in Topic.objects.filter(scheme=scheme)}
                            existing_los = {lo.code: lo for lo in LearningObjective.objects.filter(scheme=scheme)}
                            
                            # 1. Sync Topics
                            topics_by_seq = {}
                            for seq, t_data in enumerate(topics_data, 1):
                                t_title = t_data.get("title", "General")
                                if seq in existing_topics:
                                    topic = existing_topics[seq]
                                    if topic.title != t_title:
                                        topic.title = t_title
                                        topic.save(update_fields=["title"])
                                else:
                                    topic = Topic.objects.create(scheme=scheme, sequence=seq, title=t_title)
                                topics_by_seq[seq] = topic

                            total_topics += len(topics_by_seq)

                            # 2. Sync Subtopics
                            existing_subtopics = {
                                (st.topic_id, st.title): st
                                for st in Subtopic.objects.filter(topic__scheme=scheme)
                            }
                            
                            subtopics_to_create = []
                            subtopic_seq_map = {}

                            for seq, t_data in enumerate(topics_data, 1):
                                topic = topics_by_seq[seq]
                                subtopic_seq = 1
                                for lo_data in t_data.get("los", []):
                                    st_title = lo_data.get("subtopic")
                                    if st_title and (topic.id, st_title) not in existing_subtopics and (topic.id, st_title) not in subtopic_seq_map:
                                        subtopic_obj = Subtopic(
                                            topic=topic,
                                            title=st_title,
                                            sequence=subtopic_seq,
                                        )
                                        subtopics_to_create.append(subtopic_obj)
                                        subtopic_seq_map[(topic.id, st_title)] = subtopic_obj
                                        subtopic_seq += 1

                            if subtopics_to_create:
                                Subtopic.objects.bulk_create(subtopics_to_create)
                                # Refresh existing subtopics map
                                existing_subtopics = {
                                    (st.topic_id, st.title): st
                                    for st in Subtopic.objects.filter(topic__scheme=scheme)
                                }

                            total_subtopics += len(existing_subtopics)
                            scheme_subtopic_count = len(existing_subtopics)

                            # 3. Sync Learning Objectives
                            los_to_create = []
                            los_to_update = []
                            scheme_lo_count = 0

                            for seq, t_data in enumerate(topics_data, 1):
                                topic = topics_by_seq[seq]
                                lo_seq = 1
                                for lo_data in t_data.get("los", []):
                                    lo_code = lo_data.get("code", "").strip()
                                    lo_text = lo_data.get("text", "").strip()
                                    st_title = lo_data.get("subtopic")
                                    subtopic_obj = existing_subtopics.get((topic.id, st_title)) if st_title else None

                                    if lo_code in existing_los:
                                        lo_obj = existing_los[lo_code]
                                        has_changed = False
                                        if lo_obj.text != lo_text:
                                            lo_obj.text = lo_text
                                            has_changed = True
                                        if lo_obj.topic_id != topic.id:
                                            lo_obj.topic = topic
                                            has_changed = True
                                        if lo_obj.subtopic_id != (subtopic_obj.id if subtopic_obj else None):
                                            lo_obj.subtopic = subtopic_obj
                                            has_changed = True
                                        if lo_obj.sequence != lo_seq:
                                            lo_obj.sequence = lo_seq
                                            has_changed = True
                                        if has_changed:
                                            los_to_update.append(lo_obj)
                                    else:
                                        los_to_create.append(
                                            LearningObjective(
                                                scheme=scheme,
                                                topic=topic,
                                                subtopic=subtopic_obj,
                                                code=lo_code,
                                                text=lo_text,
                                                sequence=lo_seq,
                                            )
                                        )

                                    lo_seq += 1
                                    scheme_lo_count += 1

                            if los_to_create:
                                LearningObjective.objects.bulk_create(los_to_create, batch_size=200)
                            if los_to_update:
                                LearningObjective.objects.bulk_update(
                                    los_to_update,
                                    ["text", "topic", "subtopic", "sequence"],
                                    batch_size=200,
                                )

                            total_schemes += 1
                            total_los += scheme_lo_count
                            self.stdout.write(
                                f"  [OK] {sub_code} {sub_name} ({year_group}): {len(topics_by_seq)} topics, {scheme_subtopic_count} subtopics, {scheme_lo_count} LOs"
                            )
                            break
                    except Exception as e:
                        if attempt < max_retries - 1:
                            self.stdout.write(
                                self.style.WARNING(
                                    f"  Attempt {attempt + 1} for {sub_code} ({year_group}) failed: {e}. Retrying..."
                                )
                            )
                            time.sleep(1.0)
                        else:
                            self.stdout.write(
                                self.style.ERROR(
                                    f"  Failed to seed {sub_code} ({year_group}) after {max_retries} attempts: {e}"
                                )
                            )
                            raise e

        status_label = "Simulated" if dry_run else "Seeded"
        self.stdout.write(self.style.SUCCESS(f"\n=== CURRICULUM SEED COMPLETED ({status_label}) ==="))
        self.stdout.write(f"  Frameworks: {total_frameworks}")
        self.stdout.write(f"  Schemes of Work: {total_schemes}")
        self.stdout.write(f"  Topics: {total_topics}")
        self.stdout.write(f"  Subtopics: {total_subtopics}")
        self.stdout.write(f"  Learning Objectives: {total_los}")
        self.stdout.write(self.style.SUCCESS("=========================================\n"))
