"""Tests for Module 3: Apply Intelligence (classifier, filler, strategies, submitter)."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from autoapply.apply.classifier import (
    classify_page, PageAnalysis, FormField, _heuristic_classify, _parse_response,
)
from autoapply.apply.filler import fuzzy_match_field, _build_selector


# ────────────────────────────────────────────────────────────────────────────
# Classifier – heuristic fallback (no Claude needed)
# ────────────────────────────────────────────────────────────────────────────

GREENHOUSE_HTML = """
<html>
<head><title>Apply - Acme</title></head>
<body>
<script src="https://boards.greenhouse.io/..."></script>
<form id="application_form">
  <input name="first_name" type="text" required>
  <input name="last_name"  type="text" required>
  <input name="email"      type="email" required>
  <input name="phone"      type="tel">
  <input name="resume"     type="file" required>
  <button type="submit">Submit Application</button>
</form>
</body></html>
"""

LEVER_HTML = """
<html><body>
<script src="https://jobs.lever.co/..."></script>
<form>
  <input name="name"  type="text">
  <input name="email" type="email">
  <input type="file">
  <button type="submit">Submit application</button>
</form>
</body></html>
"""

JD_ONLY_HTML = """
<html><body>
<h1>Senior Security Engineer</h1>
<p>We are looking for a security engineer with 5 years of experience...</p>
<a href="/jobs/123/apply">Apply Now</a>
</body></html>
"""

WORKDAY_HTML = """
<html><body>
<script src="https://acme.myworkdayjobs.com/..."></script>
<div id="APPLY-BUTTON-GROUP">
  <button>Apply</button>
</div>
</body></html>
"""


class TestHeuristicClassifier:
    def test_greenhouse_detected_as_apply_page(self):
        result = _heuristic_classify(GREENHOUSE_HTML)
        assert result.ats_platform == "greenhouse"
        assert result.is_apply_page is True

    def test_lever_detected(self):
        result = _heuristic_classify(LEVER_HTML)
        assert result.ats_platform == "lever"
        assert result.is_apply_page is True

    def test_jd_page_not_apply_page(self):
        result = _heuristic_classify(JD_ONLY_HTML)
        assert result.is_apply_page is False
        assert result.has_apply_button is True

    def test_workday_detected(self):
        result = _heuristic_classify(WORKDAY_HTML)
        assert result.ats_platform == "workday"

    def test_empty_html(self):
        result = _heuristic_classify("")
        assert isinstance(result, PageAnalysis)
        assert result.ats_platform == "unknown"

    def test_unknown_ats(self):
        result = _heuristic_classify("<html><body><form><input type='text' name='email'></form></body></html>")
        assert result.ats_platform == "unknown"


class TestParseResponse:
    def test_full_response(self):
        data = {
            "is_apply_page": True,
            "has_apply_button": True,
            "apply_button_selector": "button[type='submit']",
            "ats_platform": "greenhouse",
            "form_fields": [
                {"name": "email", "label": "Email", "type": "email",
                 "required": True, "selector": "#email"},
            ],
        }
        result = _parse_response(data)
        assert result.is_apply_page is True
        assert result.ats_platform == "greenhouse"
        assert len(result.form_fields) == 1
        assert result.form_fields[0].field_type == "email"
        assert result.form_fields[0].required is True

    def test_missing_fields_default_gracefully(self):
        result = _parse_response({})
        assert result.is_apply_page is False
        assert result.ats_platform == "unknown"
        assert result.form_fields == []


class TestClassifyPage:
    def test_uses_heuristic_when_no_api_key(self):
        # Make get_sync_client raise ImportError so classifier falls back to
        # the ANTHROPIC_API_KEY check, which is empty → heuristic path
        import autoapply.apply.classifier as cls_mod
        orig = cls_mod.ANTHROPIC_API_KEY
        cls_mod.ANTHROPIC_API_KEY = ""
        try:
            with patch("autoapply.llm.get_sync_client", side_effect=ImportError("no module")):
                result = classify_page(GREENHOUSE_HTML)
            assert isinstance(result, PageAnalysis)
        finally:
            cls_mod.ANTHROPIC_API_KEY = orig

    def test_claude_response_parsed_correctly(self):
        mock_response_text = json.dumps({
            "is_apply_page": True,
            "has_apply_button": True,
            "apply_button_selector": "button[type='submit']",
            "ats_platform": "greenhouse",
            "form_fields": [
                {"name": "email", "label": "Email", "type": "email", "required": True, "selector": "#email"}
            ],
        })
        mock_content = MagicMock()
        mock_content.text = mock_response_text
        mock_response = MagicMock()
        mock_response.content = [mock_content]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("autoapply.llm.get_sync_client", return_value=mock_client):
            result = classify_page(GREENHOUSE_HTML)
        assert result.is_apply_page is True
        assert result.ats_platform == "greenhouse"

    def test_falls_back_on_json_error(self):
        mock_content = MagicMock()
        mock_content.text = "not valid json {{{"
        mock_response = MagicMock()
        mock_response.content = [mock_content]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("autoapply.llm.get_sync_client", return_value=mock_client):
            result = classify_page(GREENHOUSE_HTML)
        assert isinstance(result, PageAnalysis)


# ────────────────────────────────────────────────────────────────────────────
# Filler – fuzzy_match_field
# ────────────────────────────────────────────────────────────────────────────

PROFILE = {
    "first_name": "Jane",
    "last_name": "Doe",
    "email": "jane@example.com",
    "phone": "+1-555-1234",
    "linkedin_url": "https://linkedin.com/in/jane",
    "github_url": "https://github.com/jane",
    "portfolio_url": "https://jane.dev",
    "salary_range": "130000",
    "visa_sponsorship": "Yes",
    "work_authorization": "OPT",
    "graduation_date": "May 2026",
    "cover_letter": "Dear Hiring Manager...",
}


class TestFuzzyMatchField:
    def test_exact_alias_match(self):
        assert fuzzy_match_field("email", PROFILE) == "jane@example.com"

    def test_close_match(self):
        assert fuzzy_match_field("first-name", PROFILE) == "Jane"

    def test_underscore_variant(self):
        assert fuzzy_match_field("last_name", PROFILE) == "Doe"

    def test_phone_variants(self):
        assert fuzzy_match_field("telephone", PROFILE) == "+1-555-1234"
        assert fuzzy_match_field("mobile", PROFILE) == "+1-555-1234"

    def test_linkedin_variants(self):
        assert fuzzy_match_field("linkedin_url", PROFILE) == "https://linkedin.com/in/jane"
        assert fuzzy_match_field("linkedin_profile", PROFILE) == "https://linkedin.com/in/jane"

    def test_visa_field(self):
        assert fuzzy_match_field("visa_sponsorship", PROFILE) == "Yes"

    def test_unrecognised_returns_none(self):
        assert fuzzy_match_field("xyzzy_unknown_field_abc", PROFILE) is None

    def test_empty_field_name(self):
        result = fuzzy_match_field("", PROFILE)
        assert result is None or isinstance(result, str)

    def test_returns_string(self):
        val = fuzzy_match_field("email", PROFILE)
        assert isinstance(val, str)


class TestBuildSelector:
    def test_uses_explicit_selector(self):
        f = FormField(name="email", label="Email", field_type="email",
                      selector="#email-input")
        assert _build_selector(f) == "#email-input"

    def test_falls_back_to_name(self):
        f = FormField(name="email", label="Email", field_type="email", selector="")
        assert _build_selector(f) == '[name="email"]'

    def test_empty_selector_and_name(self):
        f = FormField(name="", label="", field_type="text", selector="")
        result = _build_selector(f)
        assert isinstance(result, str)


# ────────────────────────────────────────────────────────────────────────────
# Strategies
# ────────────────────────────────────────────────────────────────────────────

class TestStrategies:
    def test_get_strategy_greenhouse(self):
        from autoapply.apply.strategies import get_strategy
        from autoapply.apply.strategies.greenhouse import GreenhouseStrategy
        assert get_strategy("greenhouse") is GreenhouseStrategy

    def test_get_strategy_lever(self):
        from autoapply.apply.strategies import get_strategy
        from autoapply.apply.strategies.lever import LeverStrategy
        assert get_strategy("lever") is LeverStrategy

    def test_get_strategy_workday(self):
        from autoapply.apply.strategies import get_strategy
        from autoapply.apply.strategies.workday import WorkdayStrategy
        assert get_strategy("workday") is WorkdayStrategy

    def test_get_strategy_unknown_returns_generic(self):
        from autoapply.apply.strategies import get_strategy
        from autoapply.apply.strategies.generic import GenericStrategy
        assert get_strategy("taleo") is GenericStrategy
        assert get_strategy("icims") is GenericStrategy
        assert get_strategy("unknown") is GenericStrategy
        assert get_strategy("") is GenericStrategy

    def test_greenhouse_has_auto_submit(self):
        from autoapply.apply.strategies.greenhouse import GreenhouseStrategy
        assert GreenhouseStrategy.auto_submit is True

    def test_lever_has_auto_submit(self):
        from autoapply.apply.strategies.lever import LeverStrategy
        assert LeverStrategy.auto_submit is True

    def test_workday_auto_submit(self):
        from autoapply.apply.strategies.workday import WorkdayStrategy
        assert WorkdayStrategy.auto_submit is True

    def test_generic_no_auto_submit(self):
        from autoapply.apply.strategies.generic import GenericStrategy
        assert GenericStrategy.auto_submit is False

    @pytest.mark.asyncio
    async def test_workday_execute_returns_false(self):
        from autoapply.apply.strategies.workday import WorkdayStrategy
        strategy = WorkdayStrategy()
        page = AsyncMock()
        analysis = PageAnalysis(ats_platform="workday", form_fields=[])
        with patch("autoapply.apply.strategies.workday.fill_form", new=AsyncMock(return_value={})):
            result = await strategy.execute(page, analysis, "/tmp/r.pdf", PROFILE)
        assert result is False

    @pytest.mark.asyncio
    async def test_generic_execute_returns_false(self):
        from autoapply.apply.strategies.generic import GenericStrategy
        strategy = GenericStrategy()
        page = AsyncMock()
        analysis = PageAnalysis(ats_platform="unknown", form_fields=[])
        with patch("autoapply.apply.strategies.generic.fill_form", new=AsyncMock(return_value={})):
            result = await strategy.execute(page, analysis, "/tmp/r.pdf", PROFILE)
        assert result is False


# ────────────────────────────────────────────────────────────────────────────
# Navigator (mocked page)
# ────────────────────────────────────────────────────────────────────────────

class TestNavigator:
    @pytest.mark.asyncio
    async def test_returns_true_when_already_apply_page(self):
        from autoapply.apply.navigator import navigate_to_apply_page

        page = AsyncMock()
        page.content = AsyncMock(return_value=GREENHOUSE_HTML)
        page.url = "https://boards.greenhouse.io/co/jobs/1/apply"
        page.context = MagicMock()
        page.context.pages = [page]

        with (
            patch("autoapply.apply.navigator.classify_page") as mock_cls,
            patch("autoapply.apply.navigator.detect_auth_wall", new=AsyncMock(return_value="none")),
            patch("autoapply.apply.navigator.handle_auth", new=AsyncMock(return_value=True)),
        ):
            mock_cls.return_value = PageAnalysis(
                is_apply_page=True, has_apply_button=False,
                ats_platform="greenhouse", form_fields=[],
            )
            found, analysis = await navigate_to_apply_page(page, "https://boards.greenhouse.io/co/jobs/1")

        assert found is True
        assert analysis is not None

    @pytest.mark.asyncio
    async def test_returns_false_when_no_apply_button(self):
        from autoapply.apply.navigator import navigate_to_apply_page

        page = AsyncMock()
        page.content = AsyncMock(return_value="<html><body>Job description only</body></html>")
        page.url = "https://example.com/job"
        page.context = MagicMock()
        page.context.pages = [page]

        with (
            patch("autoapply.apply.navigator.classify_page") as mock_cls,
            patch("autoapply.apply.navigator.detect_auth_wall", new=AsyncMock(return_value="none")),
            patch("autoapply.apply.navigator.handle_auth", new=AsyncMock(return_value=True)),
        ):
            mock_cls.return_value = PageAnalysis(
                is_apply_page=False, has_apply_button=False,
                ats_platform="unknown", form_fields=[],
            )
            found, analysis = await navigate_to_apply_page(page, "https://example.com/job")

        assert found is False
        assert analysis is None


# ────────────────────────────────────────────────────────────────────────────
# Submitter
# ────────────────────────────────────────────────────────────────────────────

class TestSubmitter:
    @pytest.mark.asyncio
    async def test_dry_run_never_submits(self):
        import os, tempfile, sqlite3, autoapply.config as cfg
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        cfg.DB_PATH = tmp.name
        from autoapply.storage.database import init_db, insert_job
        init_db()
        insert_job(uuid="sub-dry", company_name="C", job_url="https://x.com",
                   sig_hash="s-dry")

        from autoapply.apply.submitter import decide_and_submit
        page = AsyncMock()
        analysis = PageAnalysis(is_apply_page=True, ats_platform="greenhouse", form_fields=[])

        with patch("autoapply.apply.submitter.fill_form", new=AsyncMock(return_value={})):
            submitted, status = await decide_and_submit(
                page, analysis, "sub-dry", "/tmp/r.pdf",
                profile=PROFILE, dry_run=True,
            )
        assert submitted is False
        assert status == "dry_run"

    @pytest.mark.asyncio
    async def test_greenhouse_triggers_auto_submit_path(self):
        import os, tempfile, autoapply.config as cfg
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        cfg.DB_PATH = tmp.name
        from autoapply.storage.database import init_db, insert_job
        init_db()
        insert_job(uuid="sub-gh", company_name="C", job_url="https://y.com", sig_hash="s-gh")

        from autoapply.apply.submitter import decide_and_submit
        from autoapply.apply.strategies.greenhouse import GreenhouseStrategy
        page = AsyncMock()
        analysis = PageAnalysis(is_apply_page=True, ats_platform="greenhouse", form_fields=[])

        mock_strategy = AsyncMock()
        mock_strategy.auto_submit = True
        mock_strategy.execute = AsyncMock(return_value=True)

        with patch("autoapply.apply.submitter.get_strategy", return_value=lambda: mock_strategy):
            with patch("autoapply.apply.submitter._take_screenshot", new=AsyncMock(return_value="")):
                submitted, status = await decide_and_submit(
                    page, analysis, "sub-gh", "/tmp/r.pdf",
                    profile=PROFILE, dry_run=False,
                )
        assert submitted is True
        assert status == "applied"

    @pytest.mark.asyncio
    async def test_unknown_ats_queues_for_review(self):
        import tempfile, autoapply.config as cfg
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        cfg.DB_PATH = tmp.name
        from autoapply.storage.database import init_db, insert_job
        init_db()
        insert_job(uuid="sub-unk", company_name="C", job_url="https://z.com", sig_hash="s-unk")

        from autoapply.apply.submitter import decide_and_submit
        page = AsyncMock()
        analysis = PageAnalysis(is_apply_page=True, ats_platform="unknown", form_fields=[])

        mock_strategy = AsyncMock()
        mock_strategy.auto_submit = False
        mock_strategy.execute = AsyncMock(return_value=False)

        with patch("autoapply.apply.submitter.get_strategy", return_value=lambda: mock_strategy):
            with patch("autoapply.apply.submitter._take_screenshot", new=AsyncMock(return_value="")):
                submitted, status = await decide_and_submit(
                    page, analysis, "sub-unk", "/tmp/r.pdf",
                    profile=PROFILE, dry_run=False,
                )
        assert submitted is False
        assert status == "pending_review"
