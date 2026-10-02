from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
KNOWLEDGE_DB = PROJECT_ROOT / "knowledge" / "tax_knowledge.db"
MODEL_CACHE_DIR = PROJECT_ROOT / ".model_cache"
