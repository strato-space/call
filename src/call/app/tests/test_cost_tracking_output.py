from call.app import call as app_call


def test_extract_cost_from_output():
    assert app_call._extract_cost_from_output("Cost: 0.015 USD") == (0.015, "USD")
    assert app_call._extract_cost_from_output("Cost: 1.5") == (1.5, None)
    assert app_call._extract_cost_from_output("no cost here") is None


def test_update_cost_totals_from_output(monkeypatch):
    calls = {}

    def fake_update(cost, currency=None):
        calls["cost"] = cost
        calls["currency"] = currency
        return app_call.call_db.CostTotals(
            total_cost=cost,
            total_cost_today=cost,
            last_updated_date="today",
            currency=currency,
        )

    monkeypatch.setattr(app_call.call_db, "update_cost_totals", fake_update)
    totals = app_call._update_cost_totals_from_output("Cost: 0.123 EUR")
    assert totals is not None
    assert totals.total_cost == 0.123
    assert calls["cost"] == 0.123
    assert calls["currency"] == "EUR"

    # No cost -> no call
    calls.clear()
    totals2 = app_call._update_cost_totals_from_output("no cost")
    assert totals2 is None
    assert calls == {}
