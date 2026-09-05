from django.urls import path

from apps.planning import views

app_name = "planning"

urlpatterns = [
    path("work-plans/", views.work_plan_list, name="work_plan_list"),
    path("work-plans/<uuid:plan_id>/", views.work_plan_detail, name="work_plan_detail"),
    path(
        "work-plans/<uuid:plan_id>/submit/",
        views.work_plan_submit,
        name="work_plan_submit",
    ),
    path("work-plans/<uuid:plan_id>/pdf/", views.work_plan_pdf, name="work_plan_pdf"),
    path("lesson-plans/", views.lesson_plan_list, name="lesson_plan_list"),
    path("lesson-plans/<uuid:plan_id>/", views.lesson_plan_detail, name="lesson_plan_detail"),
    path(
        "lesson-plans/<uuid:plan_id>/submit/",
        views.lesson_plan_submit,
        name="lesson_plan_submit",
    ),
    path("lesson-plans/<uuid:plan_id>/pdf/", views.lesson_plan_pdf, name="lesson_plan_pdf"),
    path("review/", views.review_queue, name="review_queue"),
    path("review/work-plans/<uuid:plan_id>/", views.review_work_plan, name="review_work_plan"),
    path("review/lesson-plans/<uuid:plan_id>/", views.review_lesson_plan, name="review_lesson_plan"),
    path("timetables/upload/", views.timetable_upload, name="timetable_upload"),
    path("timetables/<uuid:timetable_id>/preview/", views.timetable_preview, name="timetable_preview"),
    path("timetables/<uuid:timetable_id>/confirm/", views.timetable_confirm, name="timetable_confirm"),
    path("timetables/my-schedule/", views.my_timetable, name="my_timetable"),
    path("api/timetable-slot-lookup/", views.api_timetable_slot_lookup, name="api_timetable_slot_lookup"),
]
