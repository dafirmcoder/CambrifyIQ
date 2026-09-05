from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import render

from apps.curriculum.models import (
    CurriculumFramework,
    LearningObjective,
    SchemeOfWork,
    Subtopic,
    Topic,
)


def api_frameworks(request):
    """Return all active curriculum frameworks with metadata."""
    frameworks = (
        CurriculumFramework.objects.filter(is_active=True)
        .annotate(schemes_count=Count("schemes", filter=Q(schemes__is_active=True)))
        .order_by("name")
    )
    data = [
        {
            "id": str(fw.pk),
            "code": fw.code,
            "name": fw.name,
            "publisher": fw.publisher,
            "schemes_count": fw.schemes_count,
        }
        for fw in frameworks
    ]
    return JsonResponse({"frameworks": data})


def api_schemes(request):
    """Filter schemes of work by framework, subject code, or year group."""
    framework_param = request.GET.get("framework", "").strip()
    subject_code = request.GET.get("subject_code", "").strip()
    year_group = request.GET.get("year_group", "").strip()
    query = request.GET.get("q", "").strip()

    queryset = (
        SchemeOfWork.objects.filter(is_active=True)
        .select_related("framework")
        .annotate(
            topics_count=Count("topics", distinct=True),
            los_count=Count("learning_objectives", distinct=True),
        )
    )

    if framework_param:
        if framework_param.startswith("CAMBRIDGE_"):
            queryset = queryset.filter(framework__code=framework_param)
        else:
            queryset = queryset.filter(
                Q(framework_id=framework_param) | Q(framework__code=framework_param)
            )

    if subject_code:
        queryset = queryset.filter(subject_code__iexact=subject_code)

    if year_group:
        queryset = queryset.filter(year_group__iexact=year_group)

    if query:
        queryset = queryset.filter(
            Q(subject_name__icontains=query)
            | Q(title__icontains=query)
            | Q(subject_code__icontains=query)
            | Q(year_group__icontains=query)
        )

    schemes = queryset.order_by("framework__name", "subject_name", "year_group")
    data = [
        {
            "id": str(s.pk),
            "framework_id": str(s.framework_id),
            "framework_code": s.framework.code,
            "framework_name": s.framework.name,
            "subject_code": s.subject_code,
            "subject_name": s.subject_name,
            "year_group": s.year_group,
            "title": s.title,
            "version": s.version,
            "topics_count": s.topics_count,
            "los_count": s.los_count,
        }
        for s in schemes
    ]
    return JsonResponse({"schemes": data})


def api_topics(request):
    """Filter topics by scheme."""
    scheme_id = request.GET.get("scheme", "").strip()
    if not scheme_id:
        return JsonResponse({"error": "Missing 'scheme' parameter."}, status=400)

    topics = (
        Topic.objects.filter(scheme_id=scheme_id)
        .annotate(
            subtopics_count=Count("subtopics", distinct=True),
            los_count=Count("learning_objectives", distinct=True),
        )
        .order_by("sequence")
    )

    data = [
        {
            "id": str(t.pk),
            "scheme_id": str(t.scheme_id),
            "code": t.code,
            "title": t.title,
            "sequence": t.sequence,
            "subtopics_count": t.subtopics_count,
            "los_count": t.los_count,
        }
        for t in topics
    ]
    return JsonResponse({"topics": data})


def api_subtopics(request):
    """Filter subtopics by topic or scheme."""
    topic_id = request.GET.get("topic", "").strip()
    scheme_id = request.GET.get("scheme", "").strip()

    queryset = Subtopic.objects.select_related("topic").annotate(
        los_count=Count("learning_objectives", distinct=True)
    )

    if topic_id:
        queryset = queryset.filter(topic_id=topic_id)
    elif scheme_id:
        queryset = queryset.filter(topic__scheme_id=scheme_id)
    else:
        return JsonResponse({"error": "Must specify 'topic' or 'scheme' parameter."}, status=400)

    subtopics = queryset.order_by("sequence")
    data = [
        {
            "id": str(st.pk),
            "topic_id": str(st.topic_id),
            "topic_title": st.topic.title,
            "code": st.code,
            "title": st.title,
            "sequence": st.sequence,
            "los_count": st.los_count,
        }
        for st in subtopics
    ]
    return JsonResponse({"subtopics": data})


def api_objectives(request):
    """Filter learning objectives by scheme, topic, subtopic, or keyword search."""
    scheme_id = request.GET.get("scheme", "").strip()
    topic_id = request.GET.get("topic", "").strip()
    subtopic_id = request.GET.get("subtopic", "").strip()
    query = request.GET.get("q", "").strip() or request.GET.get("search", "").strip()

    queryset = LearningObjective.objects.select_related("topic", "subtopic")

    if scheme_id:
        queryset = queryset.filter(scheme_id=scheme_id)
    if topic_id:
        queryset = queryset.filter(topic_id=topic_id)
    if subtopic_id:
        queryset = queryset.filter(subtopic_id=subtopic_id)
    if query:
        queryset = queryset.filter(Q(code__icontains=query) | Q(text__icontains=query))

    objectives = queryset.order_by("sequence", "code")[:500]
    data = [
        {
            "id": str(lo.pk),
            "scheme_id": str(lo.scheme_id),
            "topic_id": str(lo.topic_id) if lo.topic_id else None,
            "topic_title": lo.topic.title if lo.topic else "",
            "subtopic_id": str(lo.subtopic_id) if lo.subtopic_id else None,
            "subtopic_title": lo.subtopic.title if lo.subtopic else "",
            "code": lo.code,
            "text": lo.text,
            "sequence": lo.sequence,
        }
        for lo in objectives
    ]
    return JsonResponse({"objectives": data, "count": len(data)})


@login_required
def curriculum_browser(request):
    """Interactive curriculum explorer for teachers and school administrators."""
    frameworks = (
        CurriculumFramework.objects.filter(is_active=True)
        .annotate(schemes_count=Count("schemes", filter=Q(schemes__is_active=True)))
        .order_by("name")
    )
    return render(
        request,
        "curriculum/browser.html",
        {
            "frameworks": frameworks,
        },
    )

