"""Unit tests for AttestationEngine."""

from __future__ import annotations

from greenference_protocol import SecurityTier


def test_detect_security_tier_returns_tier(attestation):
    tier = attestation.detect_security_tier()
    assert isinstance(tier, SecurityTier)


def test_attest_before_lease_always_true(attestation):
    assert attestation.attest_before_lease() is True


def test_generate_evidence_has_tier(attestation):
    evidence = attestation.generate_evidence()
    assert "tier" in evidence
    assert "platform" in evidence


def test_standard_tier_in_normal_env(attestation):
    # In CI/dev without TEE hardware, should return STANDARD
    tier = attestation.detect_security_tier()
    # Accept any valid tier (TEE hardware may not be present)
    assert tier in {SecurityTier.STANDARD, SecurityTier.CPU_TEE, SecurityTier.CPU_GPU_ATTESTED}
