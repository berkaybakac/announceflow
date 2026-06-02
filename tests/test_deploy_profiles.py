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
