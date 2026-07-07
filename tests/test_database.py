import pytest
from src.database import create_chat, get_chat, get_chats, add_message

def test_create_and_get_chat():
    chat_id = create_chat("DB Test Chat")
    assert chat_id is not None
    
    chat = get_chat(chat_id)
    assert chat is not None
    assert chat["title"] == "DB Test Chat"
    assert chat["total_tokens"] == 0
    assert len(chat["messages"]) == 0

def test_add_message():
    chat_id = create_chat("Msg Test Chat")
    
    add_message(chat_id, "user", "Hello", None)
    add_message(chat_id, "ai", "Hi there", ["Step 1"])
    
    chat = get_chat(chat_id)
    assert len(chat["messages"]) == 2
    
    assert chat["messages"][0]["role"] == "user"
    assert chat["messages"][0]["content"] == "Hello"
    assert chat["messages"][0]["causal_steps"] is None
    
    assert chat["messages"][1]["role"] == "ai"
    assert chat["messages"][1]["content"] == "Hi there"
    assert chat["messages"][1]["causal_steps"] == ["Step 1"]
