import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cambrify.settings")
django.setup()

from apps.accounts.models import User
from apps.schools.models import School, Membership
from django.utils.text import slugify

print("Creating System Admin...")
sysadmin_email = "admin@system.local"
sysadmin_pass = "Admin123!"
if not User.objects.filter(email=sysadmin_email).exists():
    User.objects.create_superuser(email=sysadmin_email, password=sysadmin_pass, full_name="System Administrator")
else:
    u = User.objects.get(email=sysadmin_email)
    u.set_password(sysadmin_pass)
    u.save()

print("Creating School...")
school_name = "Cambridge International Academy"
school, created = School.objects.get_or_create(
    slug=slugify(school_name),
    defaults={
        "name": school_name,
        "code": "CIA-001",
        "country": "TZ",
        "is_active": True,
        "onboarding_complete": True
    }
)

print("Creating School Admin (Director)...")
director_email = "director@cia.local"
director_pass = "Director123!"
director, _ = User.objects.get_or_create(email=director_email, defaults={"full_name": "School Director"})
director.set_password(director_pass)
director.save()

Membership.objects.get_or_create(
    school=school,
    user=director,
    defaults={"role": Membership.Role.DIRECTOR, "is_primary": True}
)

teachers = []
for i in range(1, 5):
    email = f"teacher{i}@cia.local"
    pwd = f"Teacher{i}Pass!"
    teacher, _ = User.objects.get_or_create(email=email, defaults={"full_name": f"Teacher Number {i}"})
    teacher.set_password(pwd)
    teacher.save()
    
    Membership.objects.get_or_create(
        school=school,
        user=teacher,
        defaults={"role": Membership.Role.TEACHER}
    )
    teachers.append((email, pwd))

print("\n===============================")
print("SEED COMPLETED - LOGIN DETAILS:")
print("===============================")
print(f"System Admin:    {sysadmin_email} / {sysadmin_pass}")
print(f"School Director: {director_email} / {director_pass}")
for email, pwd in teachers:
    print(f"Teacher:         {email} / {pwd}")
print("===============================")
