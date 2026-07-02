from __future__ import annotations

from mmc_base.provenance import capture


def test_image_digest_read(tmp_path):
    p = tmp_path / "digest"
    p.write_text("sha256:abc\n")
    env = {"PROJECT_NAME": "p", "MMC_GIT_SHA": "deadbeef", "AWS_SECRET_ACCESS_KEY": "shh"}
    prov = capture(env=env, image_digest_path=p)
    assert prov["image_digest"] == "sha256:abc"
    assert prov["git_sha"] == "deadbeef"
    assert prov["env_snapshot"] == {"PROJECT_NAME": "p"}
    assert "AWS_SECRET_ACCESS_KEY" not in prov["env_snapshot"]


def test_image_digest_missing_returns_unknown(tmp_path):
    p = tmp_path / "nope"
    prov = capture(env={}, image_digest_path=p)
    assert prov["image_digest"] == "unknown"
    assert prov["git_sha"] == "unknown"
    assert prov["env_snapshot"] == {}


def test_env_snapshot_whitelist(tmp_path):
    p = tmp_path / "digest"
    p.write_text("sha256:x")
    prov = capture(
        env={
            "PROJECT_NAME": "p",
            "ANALYSER_TYPE": "bias",
            "RUN_ID": "r",
            "ENVIRONMENT": "test",
            "VARIANT": "AllTraffic",
            "SECRET": "no",
        },
        image_digest_path=p,
    )
    assert set(prov["env_snapshot"]) == {"PROJECT_NAME", "ANALYSER_TYPE", "RUN_ID", "ENVIRONMENT", "VARIANT"}
