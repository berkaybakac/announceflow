from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEPLOY_SCRIPT = ROOT / "deploy.sh"


def test_field_update_profile_is_advertised_and_allowed():
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "standard|field-update|clean-delivery" in script
    assert "Allowed values: standard, field-update, clean-delivery" in script


def test_field_update_profile_preserves_customer_state():
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = script.index('if [ "${DEPLOY_PROFILE}" = "field-update" ]; then')
    end = script.index('elif [ "${DEPLOY_PROFILE}" = "clean-delivery" ]; then')
    field_update_block = script[start:end]

    expected_excludes = {
        '".env"',
        '"config.json"',
        '"media/"',
        '"logs/"',
        '"runtime/"',
        '"announceflow.db"',
        '"announceflow.db-*"',
        '"announceflow.db*"',
        '"*.db"',
        '"*.db-wal"',
        '"*.db-shm"',
    }

    for exclude in expected_excludes:
        assert exclude in field_update_block


def test_cleanup_only_runs_for_clean_delivery_profile():
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'if [ "${DEPLOY_PROFILE}" = "clean-delivery" ]; then' in script
    assert 'if [ "${DEPLOY_PROFILE}" = "field-update" ]; then' in script
    assert 'find ${DEST_DIR}/media -mindepth 1 -delete' in script

    cleanup_start = script.index('if [ "${DEPLOY_PROFILE}" = "clean-delivery" ]; then')
    cleanup_end = script.index("# 2.2 Upload release stamp")
    cleanup_block = script[cleanup_start:cleanup_end]

    assert "field-update" not in cleanup_block


def test_field_update_skips_system_dependency_install_by_default():
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'INSTALL_SYSTEM_DEPS="${DEPLOY_INSTALL_SYSTEM_DEPS:-1}"' in script
    assert 'if [ "${DEPLOY_PROFILE}" = "field-update" ]' in script
    assert 'INSTALL_SYSTEM_DEPS="0"' in script
    assert "DEPLOY_INSTALL_SYSTEM_DEPS" in script
    assert "sudo -n true" in script


def test_field_update_skips_systemd_install_and_restarts_without_sudo_by_default():
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'INSTALL_SYSTEMD_SERVICE="${DEPLOY_INSTALL_SYSTEMD_SERVICE:-1}"' in script
    assert 'INSTALL_SYSTEMD_SERVICE="0"' in script
    assert "DEPLOY_INSTALL_SYSTEMD_SERVICE" in script
    assert "pgrep -u ${PI_USER} -f '^/usr/bin/python3 ${DEST_DIR}/main.py$'" in script
    assert 'kill -TERM \\"\\$pid\\"' in script
