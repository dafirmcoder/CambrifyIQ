from django.contrib.auth import authenticate
from rest_framework import serializers


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False, write_only=True)

    def validate(self, attrs):
        user = authenticate(
            request=self.context.get("request"),
            username=attrs["email"],
            password=attrs["password"],
        )
        if user is None:
            raise serializers.ValidationError("The email or password is incorrect.")
        attrs["user"] = user
        return attrs


class WorkPlanCreateSerializer(serializers.Serializer):
    assignment_id = serializers.UUIDField()
    academic_year_id = serializers.UUIDField()
    term_id = serializers.UUIDField()
    scheme_id = serializers.UUIDField()


class WorkPlanWeekSaveSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    topic_id = serializers.UUIDField(required=False, allow_null=True)
    subtopic_id = serializers.UUIDField(required=False, allow_null=True)
    lessons_per_week = serializers.IntegerField(required=False, min_value=0)
    objectives = serializers.ListField(child=serializers.UUIDField(), required=False)
    remarks = serializers.CharField(required=False, allow_blank=True, max_length=10000)


class WorkPlanSaveSerializer(serializers.Serializer):
    revision = serializers.IntegerField(min_value=1)
    resources = serializers.CharField(required=False, allow_blank=True, max_length=10000)
    weeks = WorkPlanWeekSaveSerializer(many=True)


class WorkPlanTransitionSerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True, max_length=5000)
