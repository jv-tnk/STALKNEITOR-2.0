from datetime import datetime

from django.utils import timezone

from core.models import SeasonConfig


def get_active_season_range():
    season = SeasonConfig.objects.filter(is_active=True).order_by("-updated_at").first()
    if not season:
        return None, None, None
    start_dt = timezone.make_aware(datetime.combine(season.start_date, datetime.min.time()))
    end_dt = timezone.make_aware(datetime.combine(season.end_date, datetime.max.time()))
    return season, start_dt, end_dt
