from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
RAW_FILES_DIR = RAW_DIR / "files"
PROCESSED_DIR = DATA_DIR / "processed"
INDEX_DIR = DATA_DIR / "index"
DB_PATH = INDEX_DIR / "knowledge.db"

SOURCE_BASE_URL = "https://hic.zihuanana.top/"
FILES_MANIFEST_URL = SOURCE_BASE_URL + "files.json"
HOME_URL = SOURCE_BASE_URL
QA_URL = SOURCE_BASE_URL + "api.php?action=qa"

USER_AGENT = "CampusOnboardingCopilot/0.1 (+corpus synchronization)"
