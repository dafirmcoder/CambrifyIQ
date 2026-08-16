from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        detail = response.data.get("detail") if isinstance(response.data, dict) else response.data
        response.data = {
            "error": {
                "status": response.status_code,
                "code": getattr(exc, "default_code", "request_error"),
                "detail": detail,
            }
        }
    return response
