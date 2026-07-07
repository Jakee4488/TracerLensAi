import sqlite3
import uuid
import json
from datetime import datetime
from typing import List, Dict, Optional
import os

DB_DIR = "/app/data"
DB_PATH = os.path.join(DB_DIR, "app.db")

def init_db():
    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR, exist_ok=True)
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id TEXT PRIMARY KEY,
            title TEXT,
            total_tokens INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT,
            role TEXT,
            content TEXT,
            causal_steps TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(chat_id) REFERENCES chats(id)
        )
    """)
    
    conn.commit()
    conn.close()

def create_chat(title: str = "New Chat") -> str:
    chat_id = str(uuid.uuid4())
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO chats (id, title) VALUES (?, ?)", (chat_id, title))
    conn.commit()
    conn.close()
    return chat_id

def get_chats() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM chats ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_chat(chat_id: str) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM chats WHERE id = ?", (chat_id,))
    chat = cursor.fetchone()
    if not chat:
        conn.close()
        return None
    
    cursor.execute("SELECT * FROM messages WHERE chat_id = ? ORDER BY created_at ASC", (chat_id,))
    messages = cursor.fetchall()
    conn.close()
    
    chat_dict = dict(chat)
    chat_dict['messages'] = []
    for msg in messages:
        msg_dict = dict(msg)
        if msg_dict['causal_steps']:
            msg_dict['causal_steps'] = json.loads(msg_dict['causal_steps'])
        chat_dict['messages'].append(msg_dict)
        
    return chat_dict

def update_chat_tokens(chat_id: str, added_tokens: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE chats SET total_tokens = total_tokens + ? WHERE id = ?", (added_tokens, chat_id))
    conn.commit()
    conn.close()

def update_chat_title(chat_id: str, new_title: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE chats SET title = ? WHERE id = ?", (new_title, chat_id))
    conn.commit()
    conn.close()

def add_message(chat_id: str, role: str, content: str, causal_steps: Optional[List[str]] = None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    steps_json = json.dumps(causal_steps) if causal_steps else None
    cursor.execute("INSERT INTO messages (chat_id, role, content, causal_steps) VALUES (?, ?, ?, ?)",
                   (chat_id, role, content, steps_json))
    conn.commit()
    conn.close()
