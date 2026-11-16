"""Every built-in template lab must apply cleanly and self-grade to 100%."""
import pytest

from app.engine import labs, templates
from app.workspace import Workspace


@pytest.mark.parametrize("tpl", templates.TEMPLATES, ids=lambda t: t["id"])
def test_template_applies_and_grades_100(tpl):
    w = Workspace()
    res = w.apply_lab(tpl["spec"])
    assert res["errors"] == [], f"{tpl['id']} apply errors: {res['errors']}"
    report = labs.grade_lab(w.net, tpl["spec"]["checks"])
    failed = [r for r in report["results"] if not r["passed"]]
    assert report["score"] == 100, f"{tpl['id']} failed checks: {failed}"


def test_list_templates_metadata():
    items = templates.list_templates()
    assert len(items) == len(templates.TEMPLATES)
    for it in items:
        assert it["id"] and it["name"] and it["objective"]
        assert it["category"] in {"CCNA", "CCNP", "CCIE"}
        assert it["devices"] > 0 and it["checks"] > 0
