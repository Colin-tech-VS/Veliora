"""Parsing robuste de l'URL proxy Decodo pour Playwright."""

import crawler.config as config
from crawler import browser


def _proxy_for(url, monkeypatch):
    monkeypatch.setattr(config, "pick_proxy", lambda: url)
    return browser._playwright_proxy()


def test_simple_decodo(monkeypatch):
    p = _proxy_for("http://sp12345:secret@fr.decodo.com:40000", monkeypatch)
    assert p == {
        "server": "http://fr.decodo.com:40000",
        "username": "sp12345",
        "password": "secret",
    }


def test_password_with_special_chars(monkeypatch):
    # Mot de passe avec ':' et '@' — découpe à la dernière '@'.
    p = _proxy_for("http://user-sp1-country-fr:p@ss:w0rd@gate.decodo.com:7000", monkeypatch)
    assert p["server"] == "http://gate.decodo.com:7000"
    assert p["username"] == "user-sp1-country-fr"
    assert p["password"] == "p@ss:w0rd"


def test_no_credentials(monkeypatch):
    p = _proxy_for("http://gate.decodo.com:7000", monkeypatch)
    assert p == {"server": "http://gate.decodo.com:7000"}


def test_none_when_empty(monkeypatch):
    assert _proxy_for("", monkeypatch) is None
