"""The built front end is committed and served by FastAPI, so the app works
from a fresh clone with no Node installed."""

import re


def test_the_app_route_serves_the_built_index(client):
    res = client.get("/app")
    assert res.status_code == 200
    assert '<div id="root">' in res.text


def test_the_built_assets_are_served(client):
    """Vite emits hashed asset names; whatever index.html references must
    resolve, or the page loads blank with 404s in the console - and a stale
    committed build is exactly how that happens."""
    index = client.get("/app").text
    referenced = re.findall(r'(?:src|href)="(/app/assets/[^"]+)"', index)
    assert referenced, "index.html references no assets - did the build run?"
    for asset in referenced:
        assert client.get(asset).status_code == 200, asset


def test_a_missing_asset_is_a_404_not_the_index(client):
    assert client.get("/app/assets/nope.js").status_code == 404
