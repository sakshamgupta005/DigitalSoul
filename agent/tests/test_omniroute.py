import httpx
import pytest

from agent.ghost.integrations.omniroute import OmniRouteClient, OmniRouteSettings


def test_gateway_probe_checks_health_models_and_inference():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-key"
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "configured-model"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    client = OmniRouteClient(OmniRouteSettings(api_key="test-key", model="configured-model"), httpx.MockTransport(handler))
    report = client.probe(inference=True)
    assert report["reachable"] and report["health"]["ok"] and report["models"]["count"] == 1
    assert report["inference"]["ok"]
    assert "test-key" not in str(report)


def test_transport_reachable_does_not_mean_model_authenticated():
    client = OmniRouteClient(OmniRouteSettings(), httpx.MockTransport(lambda request: httpx.Response(401)))
    report = client.probe(inference=True)
    assert report["reachable"]
    assert not report["models"]["ok"]
    assert not report["inference"]["attempted"]
    with pytest.raises(ValueError):
        OmniRouteSettings(base_url="http://127.0.0.1:20128/v1?api_key=secret")
