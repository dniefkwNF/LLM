"""Tests for the ``list_pets`` MCP tool.

Covers the 6 mandatory categories from ``prompt-step1.md``:

1. Normal call: all filter/sort/pagination params forwarded correctly.
2. Input validation failures (every enum + numeric bound).
3. Go REST API returns 4xx / 5xx.
4. Timeout and connection exceptions.
5. Backend returns invalid JSON or a body that fails the data model.
6. ``records`` / ``charges`` as null and as array both parse.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from pet_hospital_mcp.errors import ErrorCode
from pet_hospital_mcp.tools.list_pets import (
    ListPetsInput,
    ListPetsOutput,
    SPECIES_VALUES,
    STATUS_VALUES,
    SORT_BY_VALUES,
    _list_pets_impl,
)
from pet_hospital_mcp.rest_client import PetHospitalClient
from pet_hospital_mcp.config import Settings

from tests.conftest import BACKEND_BASE_URL, PET_RECORD_ARRAY_DATA, PET_RECORD_NULL_DATA


# ---------------------------------------------------------------------------
# 1. Normal call — params forwarded in camelCase.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_normal_call_forwards_camel_case_params(backend_settings: Settings, respx_mock):
    captured: dict[str, Any] = {}

    async def fake_list_pets(params: dict[str, Any]) -> dict[str, Any]:
        captured.update(params)
        return PET_RECORD_ARRAY_DATA["data"]

    client = PetHospitalClient(backend_settings)
    client.list_pets = fake_list_pets  # type: ignore[assignment]
    try:
        result = await _list_pets_impl(
            {
                "q": "猫瘟",
                "owner_name": "李四",
                "owner_phone": "13900002222",
                "species": "猫",
                "doctor": "王医生",
                "disease": "猫瘟",
                "status": "就诊中",
                "min": 100,
                "max": 1000,
                "sort_by": "totalCost",
                "order": "desc",
                "page": 2,
                "page_size": 15,
            },
            client,
        )
    finally:
        await client.aclose()

    assert isinstance(result, ListPetsOutput)
    assert result.total == 1
    assert result.page_size == 20  # from the fixture data
    # Camel-case mapping asserted:
    assert captured["ownerName"] == "李四"
    assert captured["ownerPhone"] == "13900002222"
    assert captured["sortBy"] == "totalCost"
    assert captured["pageSize"] == 15
    assert captured["page"] == 2
    assert captured["min"] == 100
    assert captured["max"] == 1000


@pytest.mark.asyncio
async def test_normal_call_with_only_required_defaults(
    backend_settings: Settings, respx_mock
):
    client = PetHospitalClient(backend_settings)
    client.list_pets = AsyncMock(return_value=PET_RECORD_NULL_DATA["data"])  # type: ignore[assignment]
    try:
        result = await _list_pets_impl({}, client)
    finally:
        await client.aclose()
    assert isinstance(result, ListPetsOutput)
    assert result.items[0].records is None  # null passes through
    # Empty arguments → empty params dict forwarded.
    assert client.list_pets.call_args[0][0] == {}


# ---------------------------------------------------------------------------
# 2. Input validation failures.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_validation_invalid_species(backend_settings: Settings):
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({"species": "鱼"}, client)
    finally:
        await client.aclose()
    assert "error" in result.model_dump()
    assert result.error.code == ErrorCode.VALIDATION_ERROR.value
    assert "species" in result.error.message


@pytest.mark.asyncio
async def test_validation_invalid_status(backend_settings: Settings):
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({"status": "已死亡"}, client)
    finally:
        await client.aclose()
    assert result.error.code == ErrorCode.VALIDATION_ERROR.value


@pytest.mark.asyncio
async def test_validation_invalid_sort_by(backend_settings: Settings):
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({"sort_by": "color"}, client)
    finally:
        await client.aclose()
    assert result.error.code == ErrorCode.VALIDATION_ERROR.value


@pytest.mark.asyncio
async def test_validation_invalid_order(backend_settings: Settings):
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({"order": "descending"}, client)
    finally:
        await client.aclose()
    assert result.error.code == ErrorCode.VALIDATION_ERROR.value


@pytest.mark.asyncio
async def test_validation_page_below_one(backend_settings: Settings):
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({"page": 0}, client)
    finally:
        await client.aclose()
    assert result.error.code == ErrorCode.VALIDATION_ERROR.value


@pytest.mark.asyncio
async def test_validation_page_size_out_of_range(backend_settings: Settings):
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({"page_size": 0}, client)
    finally:
        await client.aclose()
    assert result.error.code == ErrorCode.VALIDATION_ERROR.value

    # Reset and test upper bound.
    client2 = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({"page_size": 501}, client2)
    finally:
        await client2.aclose()
    assert result.error.code == ErrorCode.VALIDATION_ERROR.value


@pytest.mark.asyncio
async def test_validation_min_greater_than_max(backend_settings: Settings):
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({"min": 1000, "max": 100}, client)
    finally:
        await client.aclose()
    assert result.error.code == ErrorCode.VALIDATION_ERROR.value


@pytest.mark.asyncio
async def test_validation_negative_min(backend_settings: Settings):
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({"min": -5}, client)
    finally:
        await client.aclose()
    assert result.error.code == ErrorCode.VALIDATION_ERROR.value


@pytest.mark.asyncio
async def test_validation_nan_rejected(backend_settings: Settings):
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({"min": float("nan")}, client)
    finally:
        await client.aclose()
    assert result.error.code == ErrorCode.VALIDATION_ERROR.value


@pytest.mark.asyncio
async def test_validation_infinity_rejected(backend_settings: Settings):
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({"max": float("inf")}, client)
    finally:
        await client.aclose()
    assert result.error.code == ErrorCode.VALIDATION_ERROR.value


@pytest.mark.asyncio
async def test_validation_unknown_field_rejected(backend_settings: Settings):
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({"unexpected_field": "x"}, client)
    finally:
        await client.aclose()
    assert result.error.code == ErrorCode.VALIDATION_ERROR.value


@pytest.mark.parametrize("species", SPECIES_VALUES)
def test_input_model_accepts_all_valid_species(species: str):
    parsed = ListPetsInput.model_validate({"species": species})
    assert parsed.species == species


@pytest.mark.parametrize("status", STATUS_VALUES)
def test_input_model_accepts_all_valid_status(status: str):
    parsed = ListPetsInput.model_validate({"status": status})
    assert parsed.status == status


@pytest.mark.parametrize("sort_by", SORT_BY_VALUES)
def test_input_model_accepts_all_valid_sort_by(sort_by: str):
    parsed = ListPetsInput.model_validate({"sort_by": sort_by})
    assert parsed.sort_by == sort_by


# ---------------------------------------------------------------------------
# 3. Backend returns 4xx / 5xx → BACKEND_API_ERROR.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_backend_4xx_returns_backend_api_error(
    backend_settings: Settings, respx_mock
):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        return_value=httpx.Response(404, json={"code": 404, "message": "not found"})
    )
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({"page": 1}, client)
    finally:
        await client.aclose()
    assert result.error.code == ErrorCode.BACKEND_API_ERROR.value


@pytest.mark.asyncio
async def test_backend_5xx_returns_backend_api_error(
    backend_settings: Settings, respx_mock
):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        return_value=httpx.Response(500, text="boom")
    )
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({}, client)
    finally:
        await client.aclose()
    assert result.error.code == ErrorCode.BACKEND_API_ERROR.value


# ---------------------------------------------------------------------------
# 4. Timeout / connection exceptions.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_backend_timeout_returns_backend_timeout(
    backend_settings: Settings, respx_mock
):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        side_effect=httpx.TimeoutException("slow")
    )
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({}, client)
    finally:
        await client.aclose()
    assert result.error.code == ErrorCode.BACKEND_TIMEOUT.value


@pytest.mark.asyncio
async def test_backend_unavailable_returns_backend_unavailable(
    backend_settings: Settings, respx_mock
):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        side_effect=httpx.ConnectError("no host")
    )
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({}, client)
    finally:
        await client.aclose()
    assert result.error.code == ErrorCode.BACKEND_UNAVAILABLE.value


# ---------------------------------------------------------------------------
# 5. Backend returns invalid JSON / mismatched data model.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_backend_invalid_json_returns_backend_invalid_response(
    backend_settings: Settings, respx_mock
):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        return_value=httpx.Response(200, content=b"not json")
    )
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({}, client)
    finally:
        await client.aclose()
    assert result.error.code == ErrorCode.BACKEND_INVALID_RESPONSE.value


@pytest.mark.asyncio
async def test_backend_data_missing_items_returns_backend_invalid_response(
    backend_settings: Settings, respx_mock
):
    # items is a non-list type → ListPetsOutput validation fails.
    bad_data = {"items": "not-a-list", "total": 1, "page": 1, "pageSize": 20, "totalPages": 1, "totalCost": 0.0}
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        return_value=httpx.Response(
            200,
            json={"code": 200, "message": "ok", "data": bad_data, "time": "x"},
        )
    )
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({}, client)
    finally:
        await client.aclose()
    assert result.error.code == ErrorCode.BACKEND_INVALID_RESPONSE.value


# ---------------------------------------------------------------------------
# 6. records / charges null vs array both parse.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_records_charges_null_parses(backend_settings: Settings, respx_mock):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        return_value=httpx.Response(200, json=PET_RECORD_NULL_DATA)
    )
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({}, client)
    finally:
        await client.aclose()
    assert isinstance(result, ListPetsOutput)
    pet = result.items[0]
    assert pet.records is None
    assert pet.charges is None


@pytest.mark.asyncio
async def test_records_charges_array_parses(backend_settings: Settings, respx_mock):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        return_value=httpx.Response(200, json=PET_RECORD_ARRAY_DATA)
    )
    client = PetHospitalClient(backend_settings)
    try:
        result = await _list_pets_impl({}, client)
    finally:
        await client.aclose()
    assert isinstance(result, ListPetsOutput)
    pet = result.items[0]
    assert pet.records is not None and len(pet.records) == 1
    assert pet.charges is not None and len(pet.charges) == 1
    assert pet.records[0].diagnosis == "猫瘟"
    assert pet.charges[0].item == "血常规"
