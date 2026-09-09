"""Tests del contexto TLS y de la deteccion del fallo de certificados en macOS."""

import ssl
import urllib.error
from unittest.mock import patch

import pytest

from betbot.net import CERT_HELP, is_certificate_error, ssl_context
from betbot.odds.the_odds_api import OddsAPIError, TheOddsAPI
from betbot.types import Market, Sport

MACOS_ERROR = (
    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
    "unable to get local issuer certificate (_ssl.c:1082)"
)


def test_context_verifies_certificates():
    """Nunca servir un contexto sin verificacion: sin ella, un intermediario
    podria alterar las odds de las que dependen todas las decisiones."""
    ctx = ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname


def test_context_is_cached():
    assert ssl_context() is ssl_context()


def test_detects_certificate_error_directly():
    assert is_certificate_error(ssl.SSLCertVerificationError(MACOS_ERROR))


def test_detects_certificate_error_by_message():
    assert is_certificate_error(OSError(MACOS_ERROR))


def test_detects_certificate_error_through_cause_chain():
    """urllib envuelve el error de SSL, asi que hay que mirar la cadena entera."""
    try:
        raise urllib.error.URLError(ssl.SSLCertVerificationError(MACOS_ERROR))
    except urllib.error.URLError as e:
        assert is_certificate_error(e)


def test_ignores_unrelated_errors():
    assert not is_certificate_error(OSError("connection timed out"))
    assert not is_certificate_error(ValueError("nada que ver"))


def test_cause_chain_with_cycle_terminates():
    """Una cadena de causas circular no debe colgar el diagnostico."""
    a = OSError("a")
    b = OSError("b")
    a.__cause__ = b
    b.__cause__ = a
    assert not is_certificate_error(a)


def test_odds_api_surfaces_the_fix_not_just_the_error():
    """El mensaje crudo de Python no menciona macOS ni certificados sin
    instalar, asi que parece un fallo de red o de la API key. El cliente tiene
    que explicar que hacer."""
    err = urllib.error.URLError(ssl.SSLCertVerificationError(MACOS_ERROR))
    with patch("urllib.request.urlopen", side_effect=err), pytest.raises(OddsAPIError) as exc:
        TheOddsAPI("fake-key").fetch_events(Sport.NBA, [Market.MONEYLINE])
    assert "certifi" in str(exc.value)
    assert "Install Certificates" in str(exc.value)


def test_ordinary_network_error_stays_short():
    """Un timeout normal no debe soltar el tocho de los certificados."""
    with (
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")),
        pytest.raises(OddsAPIError) as exc,
    ):
        TheOddsAPI("fake-key").fetch_events(Sport.NBA, [Market.MONEYLINE])
    assert "certifi" not in str(exc.value)


def test_fetcher_does_not_retry_certificate_errors(tmp_path):
    """Un fallo de certificados es de configuracion: reintentar cuatro veces con
    espera exponencial solo hace esperar 30 segundos para el mismo error."""
    from betbot.ingest.http import CachedFetcher, FetchError

    fetcher = CachedFetcher(cache_dir=tmp_path, min_interval=0)
    err = urllib.error.URLError(ssl.SSLCertVerificationError(MACOS_ERROR))
    with (
        patch("urllib.request.urlopen", side_effect=err) as mock,
        pytest.raises(FetchError) as exc,
    ):
        fetcher.get_text("https://example.com/x")
    assert mock.call_count == 1
    assert "certifi" in str(exc.value)


def test_help_text_never_suggests_disabling_verification():
    lowered = CERT_HELP.lower()
    assert "verify=false" not in lowered
    assert "_create_unverified" not in lowered
    assert "NO desactives" in CERT_HELP
