"""
Tests for restocking API endpoints and the forecast fields they depend on.
"""
import pytest

import main


@pytest.fixture(autouse=True)
def clear_restocking_orders():
    """Orders live in module state, so isolate every test."""
    main.restocking_orders.clear()
    yield
    main.restocking_orders.clear()


def make_item(sku="WDG-001", quantity=10, unit_cost=42.5, lead_time_days=14):
    return {
        "sku": sku,
        "name": f"Item {sku}",
        "quantity": quantity,
        "unit_cost": unit_cost,
        "lead_time_days": lead_time_days,
    }


class TestDemandForecastPricing:
    """Forecast records must carry the fields restocking relies on."""

    def test_forecasts_include_cost_and_lead_time(self, client):
        response = client.get("/api/demand")
        assert response.status_code == 200
        data = response.json()
        assert len(data) > 0
        for forecast in data:
            assert forecast["unit_cost"] is not None
            assert forecast["unit_cost"] > 0
            assert forecast["lead_time_days"] is not None
            assert forecast["lead_time_days"] > 0


class TestRestockingOrderEndpoints:
    """Tests for POST/GET /api/restocking-orders."""

    def test_list_starts_empty(self, client):
        response = client.get("/api/restocking-orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_order_success(self, client):
        payload = {
            "items": [
                make_item("WDG-001", quantity=10, unit_cost=42.5, lead_time_days=14),
                make_item("FLT-405", quantity=100, unit_cost=8.4, lead_time_days=5),
            ],
            "budget": 5000,
        }
        response = client.post("/api/restocking-orders", json=payload)
        assert response.status_code == 201
        order = response.json()
        assert order["order_number"].startswith("RST-")
        assert order["order_number"] == "RST-0001"
        assert order["total_cost"] == pytest.approx(10 * 42.5 + 100 * 8.4)
        assert order["budget"] == 5000
        assert order["status"] == "Submitted"
        assert len(order["items"]) == 2

    def test_lead_time_is_max_of_items(self, client):
        payload = {
            "items": [
                make_item("A", lead_time_days=5),
                make_item("B", lead_time_days=21),
                make_item("C", lead_time_days=9),
            ],
            "budget": 10000,
        }
        order = client.post("/api/restocking-orders", json=payload).json()
        assert order["lead_time_days"] == 21

    def test_expected_delivery_is_submit_date_plus_lead_time(self, client):
        from datetime import datetime

        payload = {"items": [make_item(lead_time_days=10)], "budget": 10000}
        order = client.post("/api/restocking-orders", json=payload).json()
        submitted = datetime.fromisoformat(order["submitted_date"])
        expected = datetime.fromisoformat(order["expected_delivery"])
        assert (expected - submitted).days == 10

    def test_total_is_computed_server_side(self, client):
        """A client cannot under-report cost: the server recomputes the total."""
        payload = {
            "items": [make_item(quantity=100, unit_cost=50.0)],
            "budget": 1000,
        }
        # True total is 5000, well over the 1000 budget
        response = client.post("/api/restocking-orders", json=payload)
        assert response.status_code == 400

    def test_over_budget_returns_400(self, client):
        payload = {"items": [make_item(quantity=10, unit_cost=100.0)], "budget": 999.99}
        response = client.post("/api/restocking-orders", json=payload)
        assert response.status_code == 400
        assert "budget" in response.json()["detail"].lower()
        assert client.get("/api/restocking-orders").json() == []

    def test_exact_budget_is_allowed(self, client):
        payload = {"items": [make_item(quantity=10, unit_cost=100.0)], "budget": 1000}
        response = client.post("/api/restocking-orders", json=payload)
        assert response.status_code == 201

    def test_empty_items_returns_422(self, client):
        response = client.post("/api/restocking-orders", json={"items": [], "budget": 1000})
        assert response.status_code == 422

    @pytest.mark.parametrize("quantity", [0, -5])
    def test_non_positive_quantity_returns_422(self, client, quantity):
        payload = {"items": [make_item(quantity=quantity)], "budget": 1000}
        response = client.post("/api/restocking-orders", json=payload)
        assert response.status_code == 422

    def test_missing_fields_return_422(self, client):
        response = client.post("/api/restocking-orders", json={"budget": 1000})
        assert response.status_code == 422

    def test_submitted_orders_listed_newest_first(self, client):
        for sku in ["AAA", "BBB", "CCC"]:
            client.post(
                "/api/restocking-orders",
                json={"items": [make_item(sku, quantity=1, unit_cost=1.0)], "budget": 100},
            )
        orders = client.get("/api/restocking-orders").json()
        assert [o["order_number"] for o in orders] == ["RST-0003", "RST-0002", "RST-0001"]
        assert orders[0]["items"][0]["sku"] == "CCC"

    def test_response_structure(self, client):
        client.post(
            "/api/restocking-orders",
            json={"items": [make_item()], "budget": 1000},
        )
        order = client.get("/api/restocking-orders").json()[0]
        for field in [
            "id", "order_number", "items", "total_cost", "budget",
            "submitted_date", "lead_time_days", "expected_delivery", "status",
        ]:
            assert field in order
