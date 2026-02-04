import pytest
import requests_mock
from app import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_version_endpoint(client):
    """Test the /version endpoint returns correct data."""
    response = client.get('/version')
    assert response.status_code == 200
    # ✅ Updated to v0.0.2
    assert response.json == {"version": "v0.0.2"}

def test_temperature_endpoint(client):
    """Test the /temperature endpoint returns valid data."""
    with requests_mock.Mocker() as m:
        # Mocking the OpenSenseMap API response
        m.get(requests_mock.ANY, json={
            "sensors": [{"title": "Temperatur", "unit": "°C", "lastMeasurement": {
                "value": "22.5",
                "createdAt": "2024-01-01T12:00:00.000Z"
            }}]
        })
        
        response = client.get('/temperature')
        assert response.status_code == 200
        data = response.json
        assert "average_temperature" in data
        assert data["average_temperature"] == 22.5
        assert "status" in data

def test_temperature_status_cold(client):
    """Test status logic: Too Cold (<10)."""
    with requests_mock.Mocker() as m:
        m.get(requests_mock.ANY, json={
            "sensors": [{"title": "Temperatur", "unit": "°C", "lastMeasurement": {
                "value": "5.0",
                "createdAt": "2099-01-01T00:00:00.000Z"
            }}]
        })
        response = client.get('/temperature')
        assert response.status_code == 200
        assert response.json['status'] == "Too Cold"

def test_temperature_status_good(client):
    """Test status logic: Good (11-36)."""
    with requests_mock.Mocker() as m:
        m.get(requests_mock.ANY, json={
            "sensors": [{"title": "Temperatur", "unit": "°C", "lastMeasurement": {
                "value": "25.0",
                "createdAt": "2099-01-01T00:00:00.000Z"
            }}]
        })
        response = client.get('/temperature')
        assert response.status_code == 200
        assert response.json['status'] == "Good"

def test_temperature_status_hot(client):
    """Test status logic: Too Hot (>37)."""
    with requests_mock.Mocker() as m:
        m.get(requests_mock.ANY, json={
            "sensors": [{"title": "Temperatur", "unit": "°C", "lastMeasurement": {
                "value": "40.0",
                "createdAt": "2099-01-01T00:00:00.000Z"
            }}]
        })
        response = client.get('/temperature')
        assert response.status_code == 200
        assert response.json['status'] == "Too Hot"
