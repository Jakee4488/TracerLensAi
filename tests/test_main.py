from fastapi.testclient import TestClient

def test_health_check(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_create_chat(client: TestClient):
    response = client.post("/api/chats", json={"title": "Test Chat"})
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert data["title"] == "Test Chat"

def test_get_chats(client: TestClient):
    # First create a chat
    client.post("/api/chats", json={"title": "My Chat"})
    
    # Then get all chats
    response = client.get("/api/chats")
    assert response.status_code == 200
    chats = response.json()
    assert len(chats) >= 1
    assert any(c["title"] == "My Chat" for c in chats)
