from django.urls import path

from apps.curriculum import views

app_name = "curriculum"

urlpatterns = [
    path("", views.curriculum_browser, name="browser"),
    path("api/frameworks/", views.api_frameworks, name="api_frameworks"),
    path("api/schemes/", views.api_schemes, name="api_schemes"),
    path("api/topics/", views.api_topics, name="api_topics"),
    path("api/subtopics/", views.api_subtopics, name="api_subtopics"),
    path("api/objectives/", views.api_objectives, name="api_objectives"),
]
