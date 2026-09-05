"""Timetable provisioning service.

Commits confirmed teacher timetable schedule slots, creating or linking:
- Subject records
- SchoolClass records
- TeacherAssignment records
- TeacherScheduleSlot records
"""

from datetime import datetime, time
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError, PermissionDenied

from apps.curriculum.models import SchemeOfWork
from apps.schools.models import (
    School,
    Subject,
    SchoolClass,
    TeacherAssignment,
    TeacherTimetable,
    TeacherScheduleSlot,
    AuditLog,
)


def commit_teacher_timetable(*, timetable, subject_mappings, confirmed_slots, actor):
    """Commit teacher timetable slots and auto-provision subjects, classes, assignments, and schedule slots."""
    if timetable.teacher_id != actor.id:
        raise PermissionDenied("You can only confirm your own timetable.")
    
    school = timetable.school
    today = timezone.localdate()

    with transaction.atomic():
        # 1. Resolve and provision Subjects
        # subject_mappings: dict of raw_code -> {subject_id, subject_name, cambridge_code}
        resolved_subjects = {} # raw_key -> Subject instance

        for raw_code, mapping in subject_mappings.items():
            subj_id = mapping.get("subject_id")
            subj_name = (mapping.get("subject_name") or raw_code).strip()
            cam_code = (mapping.get("cambridge_code") or "").strip()

            subject = None
            if subj_id:
                subject = Subject.objects.filter(id=subj_id, school=school).first()

            if not subject and subj_name:
                subject = Subject.objects.filter(school=school, name__iexact=subj_name).first()

            if not subject and raw_code:
                subject = Subject.objects.filter(school=school, code__iexact=raw_code).first()

            if not subject:
                # Find matching Cambridge Scheme if cambridge_code not explicit
                if not cam_code:
                    scheme = SchemeOfWork.objects.filter(subject_name__icontains=subj_name, is_active=True).first()
                    if scheme:
                        cam_code = scheme.subject_code

                # Auto-provision subject
                code_to_use = raw_code if len(raw_code) <= 32 else raw_code[:32]
                subject = Subject.objects.create(
                    school=school,
                    name=subj_name,
                    code=code_to_use,
                    cambridge_code=cam_code or code_to_use,
                    is_active=True,
                )

            resolved_subjects[raw_code.upper()] = subject

        # 2. Resolve and provision Classes
        resolved_classes = {} # class_name_lower -> SchoolClass instance
        for slot in confirmed_slots:
            cls_name = (slot.get("class_name") or "Grade 7").strip()
            cls_key = cls_name.lower()
            if cls_key not in resolved_classes:
                yr_group = (slot.get("year_group") or "").strip()
                school_class = SchoolClass.objects.filter(school=school, name__iexact=cls_name).first()
                if not school_class:
                    school_class = SchoolClass.objects.create(
                        school=school,
                        name=cls_name,
                        year_group=yr_group,
                        boys_count=12,
                        girls_count=12,
                        is_active=True,
                    )
                resolved_classes[cls_key] = school_class

        # 3. Resolve and provision TeacherAssignments
        # (subject, school_class) -> TeacherAssignment
        resolved_assignments = {}
        created_assignments = []

        for slot in confirmed_slots:
            raw_subj = (slot.get("subject_raw") or slot.get("subject_name") or "Subject").strip().upper()
            subject = resolved_subjects.get(raw_subj)
            if not subject:
                # Fallback: find or create subject by slot subject_name
                slot_subj_name = (slot.get("subject_name") or raw_subj).strip()
                subject = Subject.objects.filter(school=school, name__iexact=slot_subj_name).first()
                if not subject:
                    subject = Subject.objects.create(
                        school=school,
                        name=slot_subj_name,
                        code=raw_subj[:32],
                        cambridge_code=raw_subj[:32],
                        is_active=True,
                    )
                resolved_subjects[raw_subj] = subject

            cls_name = (slot.get("class_name") or "Grade 7").strip()
            school_class = resolved_classes[cls_name.lower()]

            pair_key = (subject.id, school_class.id)
            if pair_key not in resolved_assignments:
                assignment = TeacherAssignment.objects.filter(
                    school=school,
                    teacher=actor,
                    subject=subject,
                    school_class=school_class,
                    is_active=True,
                ).first()

                if not assignment:
                    assignment = TeacherAssignment.objects.create(
                        school=school,
                        teacher=actor,
                        subject=subject,
                        school_class=school_class,
                        effective_from=today,
                        is_active=True,
                    )
                    created_assignments.append(assignment)
                resolved_assignments[pair_key] = assignment

        # 4. Create TeacherScheduleSlots
        # Remove any previous slots for this timetable
        TeacherScheduleSlot.objects.filter(timetable=timetable).delete()
        created_slots = []

        for slot in confirmed_slots:
            raw_subj = (slot.get("subject_raw") or slot.get("subject_name") or "Subject").strip().upper()
            subject = resolved_subjects[raw_subj]
            cls_name = (slot.get("class_name") or "Grade 7").strip()
            school_class = resolved_classes[cls_name.lower()]
            assignment = resolved_assignments[(subject.id, school_class.id)]

            day_of_week = int(slot.get("day_of_week", 0))
            
            # Parse start and end time
            st_str = slot.get("start_time", "08:00")
            et_str = slot.get("end_time", "08:45")

            try:
                st_parts = [int(p) for p in st_str.split(":")[:2]]
                start_t = time(st_parts[0], st_parts[1])
            except Exception:
                start_t = time(8, 0)

            try:
                et_parts = [int(p) for p in et_str.split(":")[:2]]
                end_t = time(et_parts[0], et_parts[1])
            except Exception:
                end_t = time(8, 45)

            if end_t <= start_t:
                # Add default 45 min duration if invalid
                end_t = time(min(23, start_t.hour + (1 if start_t.minute + 45 >= 60 else 0)), (start_t.minute + 45) % 60)

            slot_obj = TeacherScheduleSlot.objects.create(
                school=school,
                timetable=timetable,
                assignment=assignment,
                day_of_week=day_of_week,
                start_time=start_t,
                end_time=end_t,
                period_label=slot.get("period_label", ""),
                room=slot.get("room", ""),
                is_active=True,
            )
            created_slots.append(slot_obj)

        # 5. Update timetable status
        timetable.status = TeacherTimetable.Status.CONFIRMED
        timetable.confirmed_at = timezone.now()
        timetable.parsed_data = {
            "subject_mappings": subject_mappings,
            "confirmed_slots": confirmed_slots,
            "total_slots": len(created_slots),
        }
        timetable.save(update_fields=["status", "confirmed_at", "parsed_data", "updated_at"])

        AuditLog.objects.create(
            school=school,
            actor=actor,
            action="TEACHER_TIMETABLE_CONFIRMED",
            target_type="TeacherTimetable",
            target_id=str(timetable.id),
            metadata={
                "slots_count": len(created_slots),
                "assignments_created": len(created_assignments),
            },
        )

    return {
        "timetable": timetable,
        "assignments": list(resolved_assignments.values()),
        "assignments_created_count": len(created_assignments),
        "slots_created_count": len(created_slots),
    }
