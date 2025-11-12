from datetime import datetime
import sqlite3
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('slack_bot.log'),
        logging.StreamHandler()
    ]
)

# for handler in logging.getLogger().handlers:
#     handler.setFormatter(SeoulFormatter())

logger = logging.getLogger(__name__)

# --- Database Setup ---
DB_FILE = "action_items.db"

def init_db():
    """Initialize SQLite database"""
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            task_description TEXT NOT NULL,
            deadline TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            channel_id TEXT,
            message_ts TEXT,
            original_thread_ts TEXT
        )
    ''')
    conn.commit()
    conn.close()
    logger.info("Database initialized")


def check_existing_task(user_id, description):
    """Check if a task with same user_id and description already exists."""
    try:
        conn = sqlite3.connect("tasks.db")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM tasks WHERE user_id = ? AND description = ? AND status = 'pending'",
            (user_id, description)
        )
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
    except Exception as e:
        logger.error(f"Error checking duplicate task: {e}")
        return False


def save_task_to_db(user_id, user_name, task_description, deadline, channel_id, message_ts, original_thread_ts):
    """Save claimed task to database"""
    try:
        conn = sqlite3.connect('tasks.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO tasks (user_id, user_name, task_description, deadline, channel_id, message_ts, original_thread_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, user_name, task_description, deadline, channel_id, message_ts, original_thread_ts))
        conn.commit()
        task_id = cursor.lastrowid
        conn.close()
        logger.info(f"Task saved: ID={task_id}, User={user_name}, Task={task_description}")
        return task_id
    except Exception as e:
        logger.error(f"Error saving task: {str(e)}", exc_info=True)
        return None

def get_user_tasks(user_id, status=None):
    """Get all tasks for a user"""
    try:
        conn = sqlite3.connect('tasks.db')
        cursor = conn.cursor()
        
        if status:
            cursor.execute('''
                SELECT id, task_description, deadline, status, created_at 
                FROM tasks 
                WHERE user_id = ? AND status = ?
                ORDER BY created_at DESC
            ''', (user_id, status))
        else:
            cursor.execute('''
                SELECT id, task_description, deadline, status, created_at 
                FROM tasks 
                WHERE user_id = ?
                ORDER BY created_at DESC
            ''', (user_id,))
        
        tasks = cursor.fetchall()
        conn.close()
        return tasks
    except Exception as e:
        logger.error(f"Error fetching tasks: {str(e)}", exc_info=True)
        return []
    
def delete_task(task_id):
    # Delete from DB or your storage
    try:
        conn = sqlite3.connect("tasks.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Failed to delete task {task_id}: {e}")
        return False

init_db()