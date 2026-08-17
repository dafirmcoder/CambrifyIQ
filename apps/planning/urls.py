from django.urls import path

from apps.planning import views

app_name = "planning"

urlpatterns = [
    path("work-plans/", views.work_plan_list, name="work_plan_list"),
    path("work-plans/<uuid:plan_id>/", views.work_plan_detail, name="work_plan_detail"),
    path("work-plans/<uuid:plan_id>/submit/", views.work_plan_submit, name="work_plan_submit"),
    path("work-plans/<uuid:plan_id>/pdf/", views.work_plan_pdf, name="work_plan_pdf"),
    path("lesson-plans/", views.lesson_plan_list, name="lesson_plan_list"),
    path("lesson-plans/<uuid:plan_id>/", views.lesson_plan_detail, name="lesson_plan_detail"),
    path("lesson-plans/<uuid:plan_id>/submit/", views.lesson_plan_submit, name="lesson_plan_submit"),
]
