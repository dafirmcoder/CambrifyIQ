from django.urls import path

from apps.plans import views

app_name = "plans"

urlpatterns = [
    path("", views.plan_list, name="list"),
    path("review/", views.review_queue, name="review_queue"),
    path("work/create/", views.create_work_plan, name="create_work_plan"),
    path("work/<uuid:plan_id>/", views.work_plan_detail, name="work_plan"),
    path(
        "work/<uuid:plan_id>/rows/<uuid:row_id>/save/",
        views.save_work_plan_row,
        name="save_work_plan_row",
    ),
    path(
        "work/<uuid:plan_id>/resources/",
        views.save_work_plan_resources,
        name="save_work_plan_resources",
    ),
    path("lesson/create/", views.create_lesson_plan, name="create_lesson_plan"),
    path("lesson/<uuid:plan_id>/", views.lesson_plan_detail, name="lesson_plan"),
    path("lesson/<uuid:plan_id>/save/", views.save_lesson_plan, name="save_lesson_plan"),
    path("<str:kind>/<uuid:plan_id>/pdf/", views.plan_pdf, name="pdf"),
    path(
        "<str:kind>/<uuid:plan_id>/<str:action>/",
        views.transition,
        name="transition",
    ),
]
