"""Tests for :mod:`pet_hospital_mcp.rest_client`.

Covers:
* Envelope parsing on success.
* 4xx and 5xx handling (4xx is not retried; 5xx is retried).
* Timeout and connection errors.
* Invalid JSON / malformed envelope.
* ``records``/``charges`` null vs array are passed through unchanged
  (the tool layer parses them; the client just returns ``data``).
"""

from __future__ import annotations

import httpx
import pytest

from pet_hospital_mcp.errors import ErrorCode
from pet_hospital_mcp.rest_client import PetHospitalClient

from tests.conftest import BACKEND_BASE_URL, PET_RECORD_NULL_DATA, PET_RECORD_ARRAY_DATA, _envelope


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_pets_success_returns_data_envelope(
    pet_hospital_client: PetHospitalClient, respx_mock
):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        return_value=httpx.Response(200, json=PET_RECORD_NULL_DATA)
    )
    data = await pet_hospital_client.list_pets({})
    assert data["total"] == 1
    assert data["items"][0]["name"] == "旺财"
    assert data["items"][0]["records"] is None  # null passes through


@pytest.mark.asyncio
async def test_list_pets_forwards_query_params_camel_case(
    pet_hospital_client: PetHospitalClient, respx_mock
):
    route = respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        return_value=httpx.Response(200, json=PET_RECORD_ARRAY_DATA)
    )
    await pet_hospital_client.list_pets(
        {
            "q": "猫瘟",
            "ownerName": "李四",
            "ownerPhone": "13900002222",
            "species": "猫",
            "sortBy": "totalCost",
            "order": "desc",
            "page": 1,
            "pageSize": 10,
            "min": 100,
            "max": 1000,
        }
    )
    # All camelCase keys must reach the backend.
    request = route.calls[0].request
    assert request.url.params["q"] == "猫瘟"
    assert request.url.params["ownerName"] == "李四"
    assert request.url.params["ownerPhone"] == "13900002222"
    assert request.url.params["species"] == "猫"
    assert request.url.params["sortBy"] == "totalCost"
    assert request.url.params["order"] == "desc"
    assert request.url.params["page"] == "1"
    assert request.url.params["pageSize"] == "10"
    assert request.url.params["min"] == "100"
    assert request.url.params["max"] == "1000"


@pytest.mark.asyncio
async def test_list_pets_drops_empty_values(
    pet_hospital_client: PetHospitalClient, respx_mock
):
    route = respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        return_value=httpx.Response(200, json=PET_RECORD_NULL_DATA)
    )
    await pet_hospital_client.list_pets({"q": "", "species": None, "page": 1})
    request = route.calls[0].request
    # Empty / None values must not appear in the query string.
    assert "q" not in request.url.params
    assert "species" not in request.url.params
    assert request.url.params["page"] == "1"


# ---------------------------------------------------------------------------
# 4xx is not retried — returns BACKEND_API_ERROR immediately.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_pets_4xx_not_retried(pet_hospital_client: PetHospitalClient, respx_mock):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        return_value=httpx.Response(400, json=_envelope(None, code=400, message="bad"))
    )
    with pytest.raises(Exception) as exc:
        await pet_hospital_client.list_pets({"page": 1})
    assert exc.value.code is ErrorCode.BACKEND_API_ERROR
    assert respx_mock.calls.call_count == 1


# ---------------------------------------------------------------------------
# 5xx is retried, then surfaces BACKEND_API_ERROR.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_pets_5xx_retried_then_fails(
    pet_hospital_client: PetHospitalClient, respx_mock
):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        return_value=httpx.Response(503, text="unavailable")
    )
    with pytest.raises(Exception) as exc:
        await pet_hospital_client.list_pets({})
    assert exc.value.code is ErrorCode.BACKEND_API_ERROR
    # MAX_RETRIES=1 means initial + 1 retry = 2 calls.
    assert respx_mock.calls.call_count == 2


@pytest.mark.asyncio
async def test_list_pets_5xx_then_success_on_retry(
    pet_hospital_client: PetHospitalClient, respx_mock
):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        side_effect=[
            httpx.Response(500, text="boom"),
            httpx.Response(200, json=PET_RECORD_ARRAY_DATA),
        ]
    )
    data = await pet_hospital_client.list_pets({})
    assert data["total"] == 1
    assert respx_mock.calls.call_count == 2


# ---------------------------------------------------------------------------
# Timeout / connection errors
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_pets_timeout(pet_hospital_client: PetHospitalClient, respx_mock):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(side_effect=httpx.TimeoutException("slow"))
    with pytest.raises(Exception) as exc:
        await pet_hospital_client.list_pets({})
    assert exc.value.code is ErrorCode.BACKEND_TIMEOUT


@pytest.mark.asyncio
async def test_list_pets_connect_error(pet_hospital_client: PetHospitalClient, respx_mock):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        side_effect=httpx.ConnectError("no host")
    )
    with pytest.raises(Exception) as exc:
        await pet_hospital_client.list_pets({})
    assert exc.value.code is ErrorCode.BACKEND_UNAVAILABLE


# ---------------------------------------------------------------------------
# Invalid response body
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_pets_invalid_json(pet_hospital_client: PetHospitalClient, respx_mock):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        return_value=httpx.Response(200, content=b"not json", headers={"content-type": "text/plain"})
    )
    with pytest.raises(Exception) as exc:
        await pet_hospital_client.list_pets({})
    assert exc.value.code is ErrorCode.BACKEND_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_list_pets_non_dict_body(pet_hospital_client: PetHospitalClient, respx_mock):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        return_value=httpx.Response(200, json=[1, 2, 3])
    )
    with pytest.raises(Exception) as exc:
        await pet_hospital_client.list_pets({})
    assert exc.value.code is ErrorCode.BACKEND_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_list_pets_missing_data(pet_hospital_client: PetHospitalClient, respx_mock):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        return_value=httpx.Response(200, json={"code": 200, "message": "ok"})
    )
    with pytest.raises(Exception) as exc:
        await pet_hospital_client.list_pets({})
    assert exc.value.code is ErrorCode.BACKEND_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_list_pets_envelope_code_non_200(
    pet_hospital_client: PetHospitalClient, respx_mock
):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        return_value=httpx.Response(
            200, json={"code": 500, "message": "internal", "data": None}
        )
    )
    with pytest.raises(Exception) as exc:
        await pet_hospital_client.list_pets({})
    assert exc.value.code is ErrorCode.BACKEND_API_ERROR
