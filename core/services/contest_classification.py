def classify_atcoder_category(contest_id: str, title: str) -> str:
    contest_id = (contest_id or "").lower()
    title = title or ""

    if contest_id.startswith("abc"):
        return "ABC"
    if contest_id.startswith("arc"):
        return "ARC"
    if contest_id.startswith("agc"):
        return "AGC"
    if contest_id.startswith("ahc"):
        return "AHC"

    lowered = title.lower()
    if "beginner contest" in lowered:
        return "ABC"
    if "regular contest" in lowered:
        return "ARC"
    if "grand contest" in lowered:
        return "AGC"
    if "heuristic contest" in lowered:
        return "AHC"

    return "Other"


def classify_codeforces_division(title: str) -> str:
    title = title or ""

    if "Educational Codeforces Round" in title:
        return "Educational"
    if "Global Round" in title:
        return "Global"
    if "Div. 4" in title:
        return "Div4"
    if "Div. 3" in title:
        return "Div3"
    if "Div. 2" in title and "Div. 1" in title:
        return "Div2"
    if "Div. 2" in title:
        return "Div2"
    if "Div. 1" in title:
        return "Div1"

    return "Other"
