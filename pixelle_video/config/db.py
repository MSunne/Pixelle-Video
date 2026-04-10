"""
Database Configuration Store

Provides PostgreSQL-backed configuration storage using a KV flat table.
Falls back gracefully when the database is unavailable.

Table: pixelle_video_config
    - config_key: dotted path like 'llm.api_key', 'comfyui.tts.local.voice'
    - config_value: string value (numbers stored as strings, converted on load)

Environment variable:
    OMNIDRIVE_DATABASE_DSN=postgres://postgres:password@127.0.0.1:5432/omnidrive?sslmode=disable
"""

import os
from typing import Any, Dict, Optional

from loguru import logger


# SQL statements
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pixelle_video_config (
    id          SERIAL PRIMARY KEY,
    config_key  VARCHAR(255) NOT NULL UNIQUE,
    config_value TEXT,
    description VARCHAR(512) DEFAULT '',
    updated_at  TIMESTAMP DEFAULT NOW(),
    created_at  TIMESTAMP DEFAULT NOW()
);
"""

UPSERT_SQL = """
INSERT INTO pixelle_video_config (config_key, config_value, updated_at)
VALUES (%s, %s, NOW())
ON CONFLICT (config_key) DO UPDATE SET
    config_value = EXCLUDED.config_value,
    updated_at = NOW();
"""

SELECT_ALL_SQL = """
SELECT config_key, config_value FROM pixelle_video_config;
"""

SELECT_ONE_SQL = """
SELECT config_value FROM pixelle_video_config WHERE config_key = %s;
"""

DELETE_ONE_SQL = """
DELETE FROM pixelle_video_config WHERE config_key = %s;
"""


class DatabaseConfigStore:
    """
    PostgreSQL-backed configuration store.
    
    Uses a flat KV table where keys are dotted paths (e.g., 'llm.api_key').
    Automatically creates the table on first use.
    
    Usage:
        store = DatabaseConfigStore()
        if store.is_available():
            config_dict = store.load_all()
            store.save_all(config_dict)
    """
    
    def __init__(self, dsn: Optional[str] = None):
        """
        Initialize database config store.
        
        Args:
            dsn: PostgreSQL DSN. If None, reads from OMNIDRIVE_DATABASE_DSN env var.
        """
        self._dsn = dsn or os.environ.get("OMNIDRIVE_DATABASE_DSN", "")
        self._available: Optional[bool] = None
        self._table_ensured = False
    
    def is_available(self) -> bool:
        """
        Check if database is available and accessible.
        
        Returns:
            True if database connection works, False otherwise.
        """
        if not self._dsn:
            logger.debug("No database DSN configured (OMNIDRIVE_DATABASE_DSN not set)")
            return False
        
        if self._available is not None:
            return self._available
        
        try:
            conn = self._get_connection()
            conn.close()
            self._available = True
            logger.info("✅ Database config store: connected")
            return True
        except Exception as e:
            self._available = False
            logger.warning(f"⚠️ Database config store: unavailable ({e}), will use YAML fallback")
            return False
    
    def _get_connection(self):
        """Get a database connection."""
        try:
            import psycopg2
        except ImportError:
            raise ImportError(
                "psycopg2 is required for database config storage. "
                "Install it with: pip install psycopg2-binary"
            )
        return psycopg2.connect(self._dsn)
    
    def _ensure_table(self):
        """Create the config table if it doesn't exist."""
        if self._table_ensured:
            return
        
        try:
            conn = self._get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(CREATE_TABLE_SQL)
                conn.commit()
                self._table_ensured = True
                logger.debug("Database config table ensured")
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Failed to ensure config table: {e}")
            raise
    
    def load_all(self) -> Dict[str, Any]:
        """
        Load all config from database and return as nested dict.
        
        Returns:
            Nested configuration dictionary (e.g., {"llm": {"api_key": "xxx"}})
        """
        self._ensure_table()
        
        try:
            conn = self._get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(SELECT_ALL_SQL)
                    rows = cur.fetchall()
                
                # Convert flat KV rows to nested dict
                flat = {row[0]: row[1] for row in rows}
                return self._unflatten(flat)
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Failed to load config from database: {e}")
            raise
    
    def save_all(self, config_dict: Dict[str, Any]):
        """
        Save entire config dict to database (upsert all keys).
        
        Args:
            config_dict: Nested configuration dictionary
        """
        self._ensure_table()
        
        try:
            # Flatten nested dict to dotted keys
            flat = self._flatten(config_dict)
            
            conn = self._get_connection()
            try:
                with conn.cursor() as cur:
                    for key, value in flat.items():
                        # Convert value to string for storage
                        str_value = self._value_to_str(value)
                        cur.execute(UPSERT_SQL, (key, str_value))
                conn.commit()
                logger.info(f"Configuration saved to database ({len(flat)} keys)")
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Failed to save config to database: {e}")
            raise
    
    def get(self, key: str) -> Optional[str]:
        """
        Get a single config value by dotted key.
        
        Args:
            key: Dotted config key (e.g., 'llm.api_key')
            
        Returns:
            Config value as string, or None if not found
        """
        self._ensure_table()
        
        try:
            conn = self._get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(SELECT_ONE_SQL, (key,))
                    row = cur.fetchone()
                    return row[0] if row else None
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Failed to get config key '{key}': {e}")
            return None
    
    def set(self, key: str, value: Any):
        """
        Set a single config value.
        
        Args:
            key: Dotted config key
            value: Config value (will be converted to string)
        """
        self._ensure_table()
        
        try:
            conn = self._get_connection()
            try:
                with conn.cursor() as cur:
                    str_value = self._value_to_str(value)
                    cur.execute(UPSERT_SQL, (key, str_value))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Failed to set config key '{key}': {e}")
            raise
    
    # ==================== Utility Methods ====================
    
    @staticmethod
    def _flatten(d: dict, parent_key: str = '', sep: str = '.') -> dict:
        """
        Flatten nested dict to dotted key format.
        
        Example:
            {"llm": {"api_key": "xxx"}} -> {"llm.api_key": "xxx"}
        """
        items = []
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(DatabaseConfigStore._flatten(v, new_key, sep).items())
            else:
                items.append((new_key, v))
        return dict(items)
    
    @staticmethod
    def _unflatten(flat: dict, sep: str = '.') -> dict:
        """
        Unflatten dotted key format to nested dict.
        
        Example:
            {"llm.api_key": "xxx"} -> {"llm": {"api_key": "xxx"}}
        """
        result = {}
        for key, value in flat.items():
            parts = key.split(sep)
            d = result
            for part in parts[:-1]:
                if part not in d:
                    d[part] = {}
                d = d[part]
            # Try to convert string value back to appropriate type
            d[parts[-1]] = DatabaseConfigStore._str_to_value(value)
        return result
    
    @staticmethod
    def _value_to_str(value: Any) -> Optional[str]:
        """Convert a Python value to string for database storage."""
        if value is None:
            return None
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)
    
    @staticmethod
    def _str_to_value(s: Optional[str]) -> Any:
        """Convert a stored string value back to its Python type."""
        if s is None:
            return None
        
        # Boolean
        if s.lower() == "true":
            return True
        if s.lower() == "false":
            return False
        
        # None string
        if s.lower() == "none" or s == "null":
            return None
        
        # Integer
        try:
            return int(s)
        except ValueError:
            pass
        
        # Float
        try:
            return float(s)
        except ValueError:
            pass
        
        # String
        return s
