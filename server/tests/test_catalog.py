from app.catalog.sources import heuristic_score, license_name


def test_license_names():
    assert license_name("http://creativecommons.org/publicdomain/zero/1.0/") == "CC0"
    assert license_name("https://creativecommons.org/publicdomain/mark/1.0/") == "Public Domain"
    assert license_name("http://creativecommons.org/licenses/by-sa/4.0/") == "CC BY-SA 4.0"


def test_black_background_ghost_scores_above_lecture():
    good = heuristic_score("ghost floating on black background loop", "video", 30_000)
    bad = heuristic_score("halloween history lecture", "video", 60 * 60_000)
    assert good > 0.8
    assert bad < 0.2
