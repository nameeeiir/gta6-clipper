import httpx

from clipper import setup_wizard as sw


def _fake_post(status, body):
    def post(url, **kw):
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))
    return post


def test_twitch_key_checks(monkeypatch):
    monkeypatch.setattr(sw.httpx, "post", _fake_post(200, {"access_token": "x", "expires_in": 100}))
    assert sw.check_twitch("id", "secret")[0] is True
    monkeypatch.setattr(sw.httpx, "post", _fake_post(400, {"status": 400, "message": "invalid client"}))
    ok, msg = sw.check_twitch("bad", "secret")
    assert ok is False and "Client ID" in msg
    monkeypatch.setattr(sw.httpx, "post", _fake_post(403, {"status": 403, "message": "invalid client secret"}))
    ok, msg = sw.check_twitch("id", "bad")
    assert ok is False and "Client Secret is wrong" in msg


def test_env_file_editing(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# comment\nTWITCH_CLIENT_ID=\nOTHER=keep\n")
    sw.set_env_value(p, "TWITCH_CLIENT_ID", "abc")
    sw.set_env_value(p, "NEW_KEY", "xyz")
    assert p.read_text() == "# comment\nTWITCH_CLIENT_ID=abc\nOTHER=keep\nNEW_KEY=xyz\n"
    assert sw.read_env_value(p, "TWITCH_CLIENT_ID") == "abc"
    assert sw.clean('  "sk-ant-123"\n') == "sk-ant-123"


def test_setup_saves_only_verified_keys(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.example").write_text("TWITCH_CLIENT_ID=\nTWITCH_CLIENT_SECRET=\nANTHROPIC_API_KEY=\n")
    monkeypatch.setattr(sw, "check_twitch", lambda i, s: (True, "ok") if s == "good" else (False, "bad"))
    answers = iter(["myid", "wrong", "myid", "good", ""])  # first secret rejected, then accepted, skip Claude
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert sw.run() == 0
    env = (tmp_path / ".env").read_text()
    assert "TWITCH_CLIENT_ID=myid\n" in env and "TWITCH_CLIENT_SECRET=good\n" in env
