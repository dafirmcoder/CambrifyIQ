"""Cross-subject borrowing and intelligent curriculum link recommendations.

Supports cross-curricular objective borrowing across aligned Cambridge subjects:
1. Computing <-> Digital Literacy (Primary and Lower Secondary)
2. Computer Science <-> Information and Communication Technology (ICT) (IGCSE / Upper Secondary)
3. Business Studies <-> Economics and Accounting (IGCSE / Upper Secondary)
"""

from django.db.models import Q
from apps.curriculum.models import SchemeOfWork, Topic, LearningObjective

CROSS_SUBJECT_PAIRS = [
    # Pair 1: Computing <-> Digital Literacy
    ({"computing"}, {"digital literacy"}),
    # Pair 2: Computer Science <-> Information and Communication Technology / ICT
    ({"computer science"}, {"information and communication technology", "ict"}),
    # Pair 3: Business Studies <-> Economics and Accounting
    ({"business studies", "business"}, {"economics", "accounting"}),
]

TOPIC_THEME_KEYWORDS = {
    "networks_safety": {
        "keywords": ["network", "communication", "internet", "dns", "ip address", "online", "safety", "phishing", "crime", "footprint", "security", "threat"],
        "label": "Networks, Online Safety and Digital Footprints",
        "description": "Connecting network architecture and digital communication with cyber hygiene, online safety, phishing recognition, and personal responsibility.",
    },
    "data_tools": {
        "keywords": ["data", "managing data", "spreadsheet", "table", "chart", "database", "cell", "formula", "tools", "content creation", "media", "capture"],
        "label": "Data Systems, Modeling and Creative Digital Tools",
        "description": "Connecting data capture, spreadsheet modeling, and databases with productive content creation, presentation tools, and information organization.",
    },
    "algorithms_ai_world": {
        "keywords": ["algorithm", "computational thinking", "flowchart", "programming", "logic", "code", "ai", "artificial intelligence", "digital world", "automation", "bias"],
        "label": "Computational Logic, Algorithms and AI in the Digital World",
        "description": "Connecting algorithmic thinking, problem decomposition, and programming with understanding AI systems, automated decision-making, and societal impact.",
    },
    "systems_hardware": {
        "keywords": ["system", "hardware", "software", "device", "cloud", "server", "architecture", "component", "storage", "infrastructure"],
        "label": "Computer Systems, Hardware and Cloud Infrastructure",
        "description": "Connecting computer hardware and system architecture with cloud server operations, data centers, and digital ecosystems.",
    },
    "business_economy": {
        "keywords": ["business", "economic", "market", "demand", "supply", "allocation", "resource", "scarcity", "enterprise", "consumer", "producer", "trade", "finance"],
        "label": "Business Enterprise, Market Dynamics and Economic Foundations",
        "description": "Connecting business operations and enterprise decisions with foundational economic principles, market resource allocation, and macroeconomic influences.",
    },
}


def get_cross_borrowable_schemes(primary_scheme):
    """Retrieve sibling schemes that are eligible for cross-curricular objective borrowing."""
    if not primary_scheme:
        return SchemeOfWork.objects.none()

    p_subj = (primary_scheme.subject_name or "").strip().lower()

    target_subject_names = set()
    for group_a, group_b in CROSS_SUBJECT_PAIRS:
        if any(term in p_subj for term in group_a):
            target_subject_names.update(group_b)
        elif any(term in p_subj for term in group_b):
            target_subject_names.update(group_a)
            target_subject_names.update(group_b - {p_subj})

    if not target_subject_names:
        return SchemeOfWork.objects.none()

    q_filter = Q(framework_id=primary_scheme.framework_id)
    if primary_scheme.year_group:
        q_filter &= Q(year_group=primary_scheme.year_group)

    subj_q = Q()
    for name in target_subject_names:
        subj_q |= Q(subject_name__icontains=name)

    return SchemeOfWork.objects.filter(q_filter & subj_q & ~Q(id=primary_scheme.id), is_active=True).distinct()


def is_cross_subject_borrowable(primary_scheme, candidate_scheme):
    """Check if candidate_scheme is an eligible borrowing source for primary_scheme."""
    if not primary_scheme or not candidate_scheme:
        return False
    if primary_scheme.id == candidate_scheme.id:
        return True

    eligible_schemes = get_cross_borrowable_schemes(primary_scheme)
    return eligible_schemes.filter(id=candidate_scheme.id).exists()


def get_smart_cross_subject_hints(primary_scheme, companion_schemes=None):
    """Generate smart recommendations mapping topics in the primary scheme to related borrowable companion topics and objectives."""
    if companion_schemes is None:
        companion_schemes = list(get_cross_borrowable_schemes(primary_scheme))
    elif not isinstance(companion_schemes, list):
        companion_schemes = list(companion_schemes)

    if not companion_schemes:
        return {}

    hints_by_topic_id = {}
    primary_topics = list(primary_scheme.topics.prefetch_related("learning_objectives").all())

    companion_topics = []
    for cs in companion_schemes:
        for ct in cs.topics.prefetch_related("learning_objectives").all():
            companion_topics.append({
                "scheme_id": str(cs.id),
                "scheme_title": cs.title,
                "scheme_subject": cs.subject_name,
                "topic": ct,
                "objectives": list(ct.learning_objectives.all()),
            })

    for pt in primary_topics:
        pt_id_str = str(pt.id)
        pt_text = (pt.title + " " + " ".join(o.text for o in pt.learning_objectives.all())).lower()

        matched_hints = []
        for ct_data in companion_topics:
            ct = ct_data["topic"]
            ct_text = (ct.title + " " + " ".join(o.text for o in ct_data["objectives"])).lower()

            best_theme = None
            max_theme_score = 0

            for theme_key, theme_info in TOPIC_THEME_KEYWORDS.items():
                kw_matches_pt = sum(1 for kw in theme_info["keywords"] if kw in pt_text)
                kw_matches_ct = sum(1 for kw in theme_info["keywords"] if kw in ct_text)
                if kw_matches_pt > 0 and kw_matches_ct > 0:
                    score = kw_matches_pt * kw_matches_ct
                    if score > max_theme_score:
                        max_theme_score = score
                        best_theme = theme_info

            if max_theme_score > 0 and best_theme:
                relevant_objs = []
                for obj in ct_data["objectives"]:
                    obj_text_lower = (obj.code + " " + obj.text).lower()
                    obj_kw_hits = sum(1 for kw in best_theme["keywords"] if kw in obj_text_lower)
                    relevant_objs.append((obj_kw_hits, obj))

                relevant_objs.sort(key=lambda x: x[0], reverse=True)
                top_objs = [
                    {
                        "id": str(o.id),
                        "code": o.code,
                        "text": o.text,
                        "topic_title": ct.title,
                        "scheme_subject": ct_data["scheme_subject"],
                    }
                    for _, o in relevant_objs[:4]
                ]

                matched_hints.append({
                    "theme_label": best_theme["label"],
                    "theme_description": best_theme["description"],
                    "companion_scheme_title": ct_data["scheme_title"],
                    "companion_scheme_subject": ct_data["scheme_subject"],
                    "companion_topic_id": str(ct.id),
                    "companion_topic_title": ct.title,
                    "recommended_objectives": top_objs,
                })

        if matched_hints:
            hints_by_topic_id[pt_id_str] = matched_hints

    return hints_by_topic_id
