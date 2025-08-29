import os
import psycopg2
from psycopg2.extras import RealDictCursor
import json
import logging

logger = logging.getLogger(__name__)

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', '127.0.0.1'),
    'port': os.getenv('DB_PORT', '15432'),
    'database': os.getenv('DB_NAME', 'metricsdb'),
    'user': os.getenv('DB_USER', 'metricsuser'),
    'password': os.getenv('DB_PASSWORD', ''),
}

BOT_STATS_TABLE = "metricsdb"

def get_db_connection():
    """Create a database connection."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        logger.debug(f"Connected to database {DB_CONFIG['database']} at {DB_CONFIG['host']}:{DB_CONFIG['port']}")
        return conn
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return None

def init_db():
    """Initialize the database tables."""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        with conn.cursor() as cur:
            # Create the main stats table with proper structure
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_usage_stats (
                    id SERIAL PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    thumbs_up INTEGER DEFAULT 0,
                    thumbs_down INTEGER DEFAULT 0,
                    thumbs_up_log JSONB DEFAULT '{}',
                    thumbs_down_log JSONB DEFAULT '{}',
                    prev_thumbs_up INTEGER DEFAULT 0,
                    prev_thumbs_down INTEGER DEFAULT 0,
                    unique_user_count INTEGER DEFAULT 0,
                    total_calls INTEGER DEFAULT 0,
                    analyze_calls INTEGER DEFAULT 0,
                    analyze_followups INTEGER DEFAULT 0,
                    general_calls INTEGER DEFAULT 0,
                    general_followups INTEGER DEFAULT 0,
                    pdf_exports INTEGER DEFAULT 0,
                    feedback_up_reasons JSONB DEFAULT '{}',
                    feedback_down_reasons JSONB DEFAULT '{}',
                    custom_feedback JSONB DEFAULT '[]'
                )
            """)
            conn.commit()
        conn.close()
        logging.info("Database table bot_usage_stats initialized successfully")
        return True
    except Exception as e:
        logging.error(f"Failed to initialize database table: {e}")
        conn.close()
        return False

def load_stats_from_db():
    """Load stats from database - fetch the LATEST entry."""
    # Initialize database tables first
    init_db()
    
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get the LATEST stats record (most recent created_at)
            cur.execute(f"""
                SELECT * FROM {BOT_STATS_TABLE} 
                ORDER BY created_at DESC 
                LIMIT 1
            """)
            row = cur.fetchone()
            
            if row:
                # Parse JSON fields properly
                def safe_json_load(json_data):
                    try:
                        if isinstance(json_data, str):
                            return json.loads(json_data)
                        elif isinstance(json_data, (dict, list)):
                            return json_data
                        else:
                            return {}
                    except:
                        return {}
                
                return {
                    "thumbs_up": row['thumbs_up'],
                    "thumbs_down": row['thumbs_down'],
                    "thumbs_up_log": safe_json_load(row['thumbs_up_log']),
                    "thumbs_down_log": safe_json_load(row['thumbs_down_log']),
                    "prev_thumbs_up": row['prev_thumbs_up'],
                    "prev_thumbs_down": row['prev_thumbs_down'],
                    "unique_user_count": row['unique_user_count'],
                    "total_calls": row['total_calls'],
                    "analyze_calls": row['analyze_calls'],
                    "analyze_followups": row['analyze_followups'],
                    "general_calls": row['general_calls'],
                    "general_followups": row['general_followups'],
                    "pdf_exports": row['pdf_exports'],
                    "feedback_up_reasons": safe_json_load(row['feedback_up_reasons']),
                    "feedback_down_reasons": safe_json_load(row['feedback_down_reasons']),
                    "custom_feedback": safe_json_load(row['custom_feedback'])
                }
    except Exception as e:
        logger.error(f"Failed to load stats from DB: {e}")
    finally:
        conn.close()
    
    return None

def save_stats_to_db(stats_dict):
    """Save stats to database - INSERT a NEW entry each time."""
    # Initialize database tables first
    init_db()
    
    conn = get_db_connection()
    if not conn:
        logger.warning("Database connection failed, skipping DB save")
        return False
    
    try:
        with conn.cursor() as cur:
            # INSERT a NEW record with current timestamp
            cur.execute(f"""
                INSERT INTO {BOT_STATS_TABLE} (
                    thumbs_up, thumbs_down, thumbs_up_log, thumbs_down_log,
                    prev_thumbs_up, prev_thumbs_down, unique_user_count,
                    total_calls, analyze_calls, analyze_followups,
                    general_calls, general_followups, pdf_exports,
                    feedback_up_reasons, feedback_down_reasons, custom_feedback
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, created_at
            """, (
                stats_dict.get("thumbs_up", 0),
                stats_dict.get("thumbs_down", 0),
                json.dumps(stats_dict.get("thumbs_up_log", {})),
                json.dumps(stats_dict.get("thumbs_down_log", {})),
                stats_dict.get("prev_thumbs_up", 0),
                stats_dict.get("prev_thumbs_down", 0),
                stats_dict.get("unique_user_count", 0),
                stats_dict.get("total_calls", 0),
                stats_dict.get("analyze_calls", 0),
                stats_dict.get("analyze_followups", 0),
                stats_dict.get("general_calls", 0),
                stats_dict.get("general_followups", 0),
                stats_dict.get("pdf_exports", 0),
                json.dumps(stats_dict.get("feedback_up_reasons", {})),
                json.dumps(stats_dict.get("feedback_down_reasons", {})),
                json.dumps(stats_dict.get("custom_feedback", []))
            ))
            result = cur.fetchone()
            conn.commit()
            
            if result:
                logger.debug(f"Stats saved to database successfully (ID: {result[0]}, Created: {result[1]})")
            else:
                logger.debug("Stats saved to database successfully")
                
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to save stats to database: {e}")
        conn.rollback()
        conn.close()
        return False

# Initialize database when module is imported
init_db()