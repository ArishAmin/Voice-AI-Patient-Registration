def test_crud_filters_and_soft_delete(client, patient_payload):
    created = client.post("/patients", json=patient_payload)
    assert created.status_code == 201
    body = created.json()
    assert body["error"] is None
    patient_id = body["data"]["patient_id"]

    listed = client.get("/patients", params={"last_name": "o'neil"})
    assert listed.status_code == 200
    assert [p["patient_id"] for p in listed.json()["data"]] == [patient_id]

    fetched = client.get(f"/patients/{patient_id}")
    assert fetched.json()["data"]["email"] == "jane@example.com"

    updated = client.put(f"/patients/{patient_id}", json={"city": "Oakland"})
    assert updated.status_code == 200
    assert updated.json()["data"]["city"] == "Oakland"

    rejected = client.put(f"/patients/{patient_id}", json={"first_name": None})
    assert rejected.status_code == 422

    deleted = client.delete(f"/patients/{patient_id}")
    assert deleted.status_code == 200
    assert client.get(f"/patients/{patient_id}").status_code == 404
    assert client.get("/patients").json()["data"] == []


def test_server_side_validation_and_envelope(client, patient_payload):
    patient_payload["date_of_birth"] = "2999-01-01"
    patient_payload["phone_number"] = "123"
    response = client.post("/patients", json=patient_payload)
    assert response.status_code == 422
    body = response.json()
    assert body["data"] is None
    assert body["error"]["code"] == "validation_error"


def test_filter_normalizes_phone(client, patient_payload):
    client.post("/patients", json=patient_payload)
    response = client.get("/patients", params={"phone_number": "+1 (415) 555-0123"})
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1


def test_dashboard_and_health(client):
    assert client.get("/health").json() == {"data": {"status": "ok"}, "error": None}
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "Patient Registration" in dashboard.text


def test_voice_webhook_returns_twiml(client):
    response = client.post("/voice/incoming", data={"CallSid": "CA_TEST", "From": "+14155550123"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<Gather" in response.text
    assert "first and last name" in response.text

