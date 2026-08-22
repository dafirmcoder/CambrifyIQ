from django.urls import path

from apps.api import views

app_name = "api"

urlpatterns = [
    path("auth/login/", views.LoginAPIView.as_view(), name="login"),
    path("auth/logout/", views.LogoutAPIView.as_view(), name="logout"),
    path("me/", views.MeAPIView.as_view(), name="me"),
    path("me/assignments/", views.MyAssignmentsAPIView.as_view(), name="assignments"),
    path("work-plans/", views.WorkPlanListCreateAPIView.as_view(), name="work_plan_list"),
    path(
        "work-plans/<uuid:plan_id>/", views.WorkPlanDetailAPIView.as_view(), name="work_plan_detail"
    ),
    path(
        "work-plans/<uuid:plan_id>/submit/",
        views.WorkPlanSubmitAPIView.as_view(),
        name="work_plan_submit",
    ),
    path(
        "work-plans/<uuid:plan_id>/review/",
        views.WorkPlanReviewAPIView.as_view(),
        name="work_plan_review",
    ),
    path(
        "work-plans/<uuid:plan_id>/return/",
        views.WorkPlanReturnAPIView.as_view(),
        name="work_plan_return",
    ),
    path(
        "work-plans/<uuid:plan_id>/approve/",
        views.WorkPlanApproveAPIView.as_view(),
        name="work_plan_approve",
    ),
]
