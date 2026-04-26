import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import render

from core.services.ranking_refresh import force_ranking_update_for_user


@login_required
def force_ranking_update(request):
    if request.method != "POST":
        return HttpResponseBadRequest("Metodo invalido.")

    result = force_ranking_update_for_user(request.user)
    response = render(
        request,
        "core/partials/ranking_force_update_status.html",
        {
            "status": result.status,
            "message": result.message,
            "details": result.details,
            "auto_refresh": result.auto_refresh,
        },
    )
    response["HX-Trigger"] = json.dumps(
        {
            "ranking-force-refresh": {
                "status": result.status,
                "until": result.refresh_until_ms,
            }
        }
    )
    return response
