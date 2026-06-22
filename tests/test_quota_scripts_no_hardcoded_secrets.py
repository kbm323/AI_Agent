"""Regression tests for quota script secret hygiene."""

import os
import subprocess
from pathlib import Path

QUOTA_SCRIPTS = (
    Path("scripts/check_all_quota.sh"),
    Path("scripts/check_quota.sh"),
)


def test_quota_scripts_do_not_embed_provider_auth_cookies():
    """Provider dashboard auth cookies must come from env/local secret files only."""
    forbidden_fragments = ("auth=" + "Fe26", "AUTH_COOKIE=\"" + "oc_locale=")

    findings = []
    for script in QUOTA_SCRIPTS:
        text = script.read_text(encoding="utf-8")
        for fragment in forbidden_fragments:
            if fragment in text:
                findings.append(f"{script}:{fragment}")

    assert findings == []


def test_quota_scripts_read_secret_inputs_from_environment():
    """Quota scripts should support env/local credentials, not repo secrets."""
    for script in QUOTA_SCRIPTS:
        text = script.read_text(encoding="utf-8")
        assert "${OPENCODE_AUTH_COOKIE" in text
        assert "${OPENCODE_WORKSPACE_ID" in text


def test_check_quota_without_credentials_exits_unknown_success(tmp_path):
    """Missing local credentials should not fail or leak secret placeholders."""
    env = os.environ.copy()
    env.pop("OPENCODE_AUTH_COOKIE", None)
    env.pop("OPENCODE_WORKSPACE_ID", None)
    env["AI_AGENT_QUOTA_ENV_FILE"] = str(tmp_path / "missing.env")

    result = subprocess.run(
        ["bash", "scripts/check_quota.sh"],
        check=False,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "UNKNOWN" in result.stdout
    assert "OPENCODE_AUTH_COOKIE" in result.stdout
    assert "auth=" + "Fe26" not in result.stdout


def test_check_all_quota_uses_env_credentials_and_hierarchical_status(tmp_path):
    """Combined quota script should parse fake provider data without real network."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text(
        "#!/bin/sh\n"
        "cat <<'EOF'\n"
        "rollingUsage:$R[1]={status:\"ok\",resetInSec:60,usagePercent:101}\n"
        "weeklyUsage:$R[2]={status:\"ok\",resetInSec:120,usagePercent:1}\n"
        "monthlyUsage:$R[3]={status:\"ok\",resetInSec:180,usagePercent:96}\n"
        "EOF\n",
        encoding="utf-8",
    )
    (bin_dir / "codexbar").write_text(
        "#!/bin/sh\n"
        "cat <<'EOF'\n"
        "[{\"usage\":{\"primary\":{\"usedPercent\":34,\"resetDescription\":\"soon\"},"
        "\"secondary\":{\"usedPercent\":19,\"resetDescription\":\"later\"},"
        "\"tertiary\":{\"usedPercent\":0}}}]\n"
        "EOF\n",
        encoding="utf-8",
    )
    for executable in (bin_dir / "curl", bin_dir / "codexbar"):
        executable.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["AI_AGENT_QUOTA_ENV_FILE"] = str(tmp_path / "missing.env")
    env["OPENCODE_AUTH_COOKIE"] = "local-cookie"
    env["OPENCODE_WORKSPACE_ID"] = "wrk_test"

    result = subprocess.run(
        ["bash", "scripts/check_all_quota.sh"],
        check=False,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "🟡 WAIT 📦 Go: M:96% W:1% H:101% Hourly 101%" in result.stdout
    assert "✅ 🤖 Codex: M:0% W:19% H:34%" in result.stdout
    assert "auth=" + "Fe26" not in result.stdout
