"""Tests for recruiting company extraction and name cleaning."""

import pytest

from deadline_agent.awareness.recruiting_extractor import (
    _clean_company_name,
    extract_company_from_email,
    extract_company_from_filename,
    has_recruiting_signal,
    infer_status,
)


# ---------------------------------------------------------------------------
# has_recruiting_signal
# ---------------------------------------------------------------------------

class TestHasRecruitingSignal:
    def test_application_keyword(self):
        assert has_recruiting_signal("Thank you for your application to Google")

    def test_interview_keyword(self):
        assert has_recruiting_signal("Your interview has been scheduled")

    def test_no_signal(self):
        assert not has_recruiting_signal("Weekly newsletter from TechCrunch")

    def test_case_insensitive(self):
        assert has_recruiting_signal("YOUR APPLICATION has been received")


# ---------------------------------------------------------------------------
# extract_company_from_email — ATS platforms (sender domain → subject parse)
# ---------------------------------------------------------------------------

class TestExtractCompanyFromEmail:
    def test_greenhouse_sender(self):
        result = extract_company_from_email(
            sender="no-reply@greenhouse.io",
            subject="Thank you for applying to Stripe",
        )
        assert result == "Stripe"

    def test_lever_sender(self):
        result = extract_company_from_email(
            sender="notifications@hire.lever.co",
            subject="Your application to Figma",
        )
        assert result == "Figma"

    def test_workday_sender(self):
        result = extract_company_from_email(
            sender="noreply@myworkdayjobs.com",
            subject="Application Confirmation",
            snippet="Thank you for applying to Microsoft. We will review your application.",
        )
        # Company in snippet, not subject
        assert result is not None

    def test_direct_company_domain(self):
        result = extract_company_from_email(
            sender="recruiting@stripe.com",
            subject="Next steps for your application",
        )
        assert result == "Stripe"

    def test_linkedin_application_sent(self):
        result = extract_company_from_email(
            sender="jobs-noreply@linkedin.com",
            subject="Your application was sent to Google",
        )
        assert result == "Google"

    def test_indeed_format(self):
        """Indeed: 'You applied to Software Engineer - Cisco'"""
        result = extract_company_from_email(
            sender="alert@indeed.com",
            subject="You applied to Software Engineer - Cisco",
        )
        assert result == "Cisco"

    def test_company_has_received(self):
        """'Airbnb has received your application'"""
        result = extract_company_from_email(
            sender="no-reply@greenhouse.io",
            subject="Airbnb has received your application",
        )
        assert result == "Airbnb"

    def test_application_for_role_at_company(self):
        result = extract_company_from_email(
            sender="noreply@ashbyhq.com",
            subject="Your application for Software Engineer at Notion",
        )
        assert result == "Notion"

    def test_aggregator_name_rejected(self):
        """Should not return 'LinkedIn' as a company."""
        result = extract_company_from_email(
            sender="jobs@linkedin.com",
            subject="New jobs for you",
        )
        assert result is None

    def test_personal_domain_falls_to_subject(self):
        result = extract_company_from_email(
            sender="recruiter@gmail.com",
            subject="Opportunity at Netflix",
        )
        assert result == "Netflix"


# ---------------------------------------------------------------------------
# Garbage name rejection
# ---------------------------------------------------------------------------

class TestCleanCompanyName:
    def test_rejects_joining_prefix(self):
        assert _clean_company_name("joining our team") is None

    def test_rejects_your_prefix(self):
        result = _clean_company_name("Your Application for Software Engineer")
        assert result is None

    def test_rejects_job_title(self):
        assert _clean_company_name("Software Engineer") is None

    def test_strips_owner_name(self, monkeypatch):
        monkeypatch.setattr("deadline_agent.awareness.recruiting_extractor.settings.owner_name", "JudeElMasri")
        result = _clean_company_name("Aquatic Capital Management Jude")
        # Should strip "Jude" part — but "JudeElMasri" not in "Jude" alone
        # The owner_name is "JudeElMasri", so "Jude" partial won't trigger
        # This tests the full owner_name case:
        result2 = _clean_company_name("Aquatic Capital Management JudeElMasri")
        assert result2 is not None
        assert "jude" not in result2.lower()

    def test_preserves_valid_name(self):
        assert _clean_company_name("Google") == "Google"

    def test_preserves_multi_word(self):
        assert _clean_company_name("Jane Street") == "Jane Street"

    def test_rejects_empty(self):
        assert _clean_company_name("") is None

    def test_rejects_short(self):
        assert _clean_company_name("AB") is None

    def test_rejects_recruiting_signal_word(self):
        assert _clean_company_name("application") is None

    def test_rejects_doc_type_token(self):
        assert _clean_company_name("resume") is None

    def test_strips_joining_returns_company(self):
        """'joining Borderless' should clean to 'Borderless'."""
        result = _clean_company_name("joining Borderless")
        assert result == "Borderless"


# ---------------------------------------------------------------------------
# extract_company_from_filename
# ---------------------------------------------------------------------------

class TestExtractCompanyFromFilename:
    def test_owner_prefix_format(self, monkeypatch):
        monkeypatch.setattr("deadline_agent.awareness.recruiting_extractor.settings.owner_name", "JudeElMasri")
        result = extract_company_from_filename("JudeElMasriGoogleResume.pdf")
        assert result == "Google"

    def test_owner_prefix_underscore(self, monkeypatch):
        monkeypatch.setattr("deadline_agent.awareness.recruiting_extractor.settings.owner_name", "JudeElMasri")
        result = extract_company_from_filename("JudeElMasri_Stripe_Resume.pdf")
        assert result == "Stripe"

    def test_fallback_no_owner_prefix(self, monkeypatch):
        monkeypatch.setattr("deadline_agent.awareness.recruiting_extractor.settings.owner_name", "JudeElMasri")
        result = extract_company_from_filename("Google_Resume.pdf")
        assert result == "Google"

    def test_fallback_resume_prefix(self, monkeypatch):
        monkeypatch.setattr("deadline_agent.awareness.recruiting_extractor.settings.owner_name", "JudeElMasri")
        result = extract_company_from_filename("Resume_Cisco.pdf")
        assert result == "Cisco"

    def test_no_company_just_resume(self, monkeypatch):
        monkeypatch.setattr("deadline_agent.awareness.recruiting_extractor.settings.owner_name", "JudeElMasri")
        result = extract_company_from_filename("JudeElMasriResume.pdf")
        assert result is None

    def test_copy_indicator_stripped(self, monkeypatch):
        monkeypatch.setattr("deadline_agent.awareness.recruiting_extractor.settings.owner_name", "JudeElMasri")
        result = extract_company_from_filename("JudeElMasriGoogleResume (1).pdf")
        assert result == "Google"

    def test_no_owner_name_configured(self, monkeypatch):
        monkeypatch.setattr("deadline_agent.awareness.recruiting_extractor.settings.owner_name", "")
        # Fallback path should still work
        result = extract_company_from_filename("Google_Resume.pdf")
        assert result == "Google"

    def test_cl_suffix_stripped(self, monkeypatch):
        """CL (cover letter) fused to company name: NavanCL.pdf → Navan"""
        monkeypatch.setattr("deadline_agent.awareness.recruiting_extractor.settings.owner_name", "JudeElMasri")
        assert extract_company_from_filename("NavanCL.pdf") == "Navan"

    def test_cl_fused_with_owner_prefix(self, monkeypatch):
        """IXLCL with owner prefix: JudeElMasriIXLCL.pdf → IXL"""
        monkeypatch.setattr("deadline_agent.awareness.recruiting_extractor.settings.owner_name", "JudeElMasri")
        assert extract_company_from_filename("JudeElMasriIXLCL.pdf") == "IXL"

    def test_cl_fused_camelcase(self, monkeypatch):
        """LoopAICL.pdf → Loop AI (CL stripped)"""
        monkeypatch.setattr("deadline_agent.awareness.recruiting_extractor.settings.owner_name", "JudeElMasri")
        assert extract_company_from_filename("LoopAICL.pdf") == "Loop AI"


# ---------------------------------------------------------------------------
# infer_status
# ---------------------------------------------------------------------------

class TestInferStatus:
    def test_applied_to_response(self):
        assert infer_status("applied", "We'd like to move forward with next steps") == "response"

    def test_response_to_interview(self):
        assert infer_status("response", "Your phone screen is scheduled") == "interview"

    def test_closed_overrides_any(self):
        assert infer_status("interview", "Unfortunately we will not be moving forward") == "closed"

    def test_no_advancement(self):
        assert infer_status("interview", "Just a reminder about your upcoming interview") == "interview"

    def test_cannot_go_backwards(self):
        assert infer_status("interview", "application received") == "interview"
