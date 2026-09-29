"""``list_pets`` MCP tool — exposes ``GET /api/v1/pets`` to AI agents.

This is the only tool registered in phase 1.

Key design points (mandated by ``prompt-step1.md``):

* Tool name is ``snake_case`` → ``list_pets``.
* Tool input fields use ``snake_case`` and are validated by pydantic.
  The 14 supported fields map 1:1 to the Go API query params:

      q, name, owner_name, owner_phone, species, doctor, disease,
      status, min, max, sort_by, order, page, page_size

  Each is sent to the Go API in **camelCase** (``ownerName``, ``pageSize``
  …) per the real backend contract.
* Enum validation matches the Go backend exactly:

    - ``species``  : 犬 / 猫 / 兔 / 鸟 / 仓鼠 / 爬宠 / 其他
    - ``status``   : 待就诊 / 就诊中 / 住院中 / 已康复 / 慢性病随访
    - ``sort_by``  : id / name / ownerName / species / doctor / disease /
                      status / totalCost / visitCount / createdAt / updatedAt
    - ``order``    : asc / desc
* Numeric bounds: ``page >= 1``, ``1 <= page_size <= 500``,
  ``min >= 0``, ``max >= 0``, ``min <= max``.
* Unknown fields are rejected (``extra="forbid"``).
* ``NaN`` and ``Infinity`` are rejected for ``min`` / ``max``.
* Success output mirrors the Go ``data`` envelope:

      items, total, page, page_size, total_pages, total_cost

* The Go API may return ``records`` / ``charges`` as either ``null`` or
  an array; both shapes parse to ``None`` or a list respectively.
* Tool failures return :class:`ErrorOutput` with the unified
  ``{"error": {"code","message","details"}}`` envelope; success returns
  :class:`ListPetsOutput`. The SDK surfaces either via
  ``structured_content``.
"""

from __future__ import annotations

import json as _json
import math
import time
from typing import Any, Optional, Union

from mcp.server import MCPServer
from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel, Field, field_validator, model_validator

from pet_hospital_mcp.errors import ErrorCode, ErrorOutput, ToolError
from pet_hospital_mcp.logging_config import get_logger, sanitize
from pet_hospital_mcp.rest_client import PetHospitalClient

_log = get_logger("pet_hospital_mcp.tools.list_pets")

# ---------------------------------------------------------------------------
# Backend-true enum constants (mirror the Go model package exactly).
# ---------------------------------------------------------------------------
SPECIES_VALUES: list[str] = ["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"]
STATUS_VALUES: list[str] = ["待就诊", "就诊中", "住院中", "已康复", "慢性病随访"]
SORT_BY_VALUES: list[str] = [
    "id",
    "name",
    "ownerName",
    "species",
    "doctor",
    "disease",
    "status",
    "totalCost",
    "visitCount",
    "createdAt",
    "updatedAt",
]
ORDER_VALUES: list[Literal["asc", "desc"]] = ["asc", "desc"]


# ---------------------------------------------------------------------------
# Input model — strict validation, snake_case fields.
# ---------------------------------------------------------------------------
class ListPetsInput(BaseModel):
    """Input for the ``list_pets`` MCP tool.

    Every field is optional; omitting all of them returns the default
    first page (20 rows) sorted by ``createdAt desc`` on the backend.
    """

    model_config = {"extra": "forbid", "populate_by_name": True}

    q: Optional[str] = Field(default=None, description="全文检索关键词(跨字段,空格分词 AND)。")
    name: Optional[str] = Field(default=None, description="宠物姓名模糊匹配。")
    owner_name: Optional[str] = Field(default=None, description="主人姓名模糊匹配。")
    owner_phone: Optional[str] = Field(default=None, description="主人电话模糊匹配(日志中脱敏)。")
    species: Optional[str] = Field(
        default=None,
        description=f"宠物种类精确匹配。允许值: {', '.join(SPECIES_VALUES)}",
    )
    doctor: Optional[str] = Field(default=None, description="主治医生模糊匹配。")
    disease: Optional[str] = Field(default=None, description="疾病/诊断模糊匹配。")
    status: Optional[str] = Field(
        default=None,
        description=f"就诊状态精确匹配。允许值: {', '.join(STATUS_VALUES)}",
    )
    min: Optional[float] = Field(default=None, description="最低总花费(含)。不得为负。")
    max: Optional[float] = Field(default=None, description="最高总花费(含)。不得为负且 >= min。")
    sort_by: Optional[str] = Field(
        default=None,
        description=f"排序字段。允许值: {', '.join(SORT_BY_VALUES)}",
    )
    order: Optional[str] = Field(default=None, description="排序方向。允许值: asc, desc")
    page: Optional[int] = Field(default=None, ge=1, description="页码,从 1 开始,默认 1。")
    page_size: Optional[int] = Field(
        default=None,
        ge=1,
        le=500,
        description="每页条数,1-500,默认 20。",
    )

    @field_validator("species")
    @classmethod
    def _validate_species(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if v not in SPECIES_VALUES:
            raise ValueError(f"species 必须是 {SPECIES_VALUES} 之一")
        return v

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if v not in STATUS_VALUES:
            raise ValueError(f"status 必须是 {STATUS_VALUES} 之一")
        return v

    @field_validator("sort_by")
    @classmethod
    def _validate_sort_by(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if v not in SORT_BY_VALUES:
            raise ValueError(f"sortBy 必须是 {SORT_BY_VALUES} 之一")
        return v

    @field_validator("order")
    @classmethod
    def _validate_order(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if v not in ORDER_VALUES:
            raise ValueError("order 必须是 ['asc', 'desc'] 之一")
        return v

    @field_validator("min", "max")
    @classmethod
    def _validate_finite(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return None
        # Reject NaN and Infinity (JSON doesn't carry them; reject early).
        if not math.isfinite(v):
            raise ValueError("数值不能是 NaN 或 Infinity")
        if v < 0:
            raise ValueError("数值不能为负数")
        return v

    @model_validator(mode="after")
    def _validate_min_max(self) -> "ListPetsInput":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min 不能大于 max")
        return self

    def to_camel_params(self) -> dict[str, Any]:
        """Convert to the camelCase query dict the Go API expects.

        ``None`` values are kept here; the rest client drops them so the
        backend applies its own defaults. Empty strings are preserved
        (the Go API treats them as "no filter" for string fields).
        """
        params: dict[str, Any] = {}
        mapping = {
            "q": "q",
            "name": "name",
            "owner_name": "ownerName",
            "owner_phone": "ownerPhone",
            "species": "species",
            "doctor": "doctor",
            "disease": "disease",
            "status": "status",
            "min": "min",
            "max": "max",
            "sort_by": "sortBy",
            "order": "order",
            "page": "page",
            "page_size": "pageSize",
        }
        for snake, camel in mapping.items():
            v = getattr(self, snake)
            if v is not None:
                params[camel] = v
        return params


# ---------------------------------------------------------------------------
# Output models — match the Go `Result.data` + `Pet` shapes.
# ---------------------------------------------------------------------------
class MedicalRecord(BaseModel):
    """One visit record. Mirrors Go ``model.MedicalRecord``."""

    model_config = {"extra": "allow"}

    id: Optional[str] = None
    visit_date: Optional[str] = Field(default=None, alias="visitDate")
    doctor: Optional[str] = None
    diagnosis: Optional[str] = None
    symptoms: Optional[str] = None
    treatment: Optional[str] = None
    prescription: Optional[list[str]] = None
    weight_kg: Optional[float] = Field(default=None, alias="weightKg")
    temperature: Optional[float] = None
    follow_up: Optional[str] = Field(default=None, alias="followUp")
    charge: Optional[float] = 0.0
    created_at: Optional[str] = Field(default=None, alias="createdAt")


class Treatment(BaseModel):
    """One charge line. Mirrors Go ``model.Treatment``."""

    model_config = {"extra": "allow"}

    id: Optional[str] = None
    item: Optional[str] = None
    category: Optional[str] = None
    amount: Optional[float] = 0.0
    doctor: Optional[str] = None
    date: Optional[str] = None
    note: Optional[str] = None


class Pet(BaseModel):
    """One pet record returned by the Go API.

    ``records`` and ``charges`` may arrive as ``null`` or as an array;
    the model accepts both (``Optional[list[...]] = None`` and a
    validator that coerces ``null`` → ``None``).
    """

    model_config = {"extra": "allow", "populate_by_name": True}

    id: Optional[str] = None
    name: Optional[str] = None
    species: Optional[str] = None
    breed: Optional[str] = None
    gender: Optional[str] = None
    age_months: Optional[int] = Field(default=None, alias="ageMonths")
    color: Optional[str] = None
    chip_no: Optional[str] = Field(default=None, alias="chipNo")

    owner_name: Optional[str] = Field(default=None, alias="ownerName")
    owner_phone: Optional[str] = Field(default=None, alias="ownerPhone")
    owner_addr: Optional[str] = Field(default=None, alias="ownerAddr")

    doctor: Optional[str] = None
    disease: Optional[str] = None
    status: Optional[str] = None
    allergy: Optional[str] = None
    note: Optional[str] = None

    records: Optional[list[MedicalRecord]] = None
    charges: Optional[list[Treatment]] = None

    total_cost: Optional[float] = Field(default=0.0, alias="totalCost")
    visit_count: Optional[int] = Field(default=0, alias="visitCount")

    created_at: Optional[str] = Field(default=None, alias="createdAt")
    updated_at: Optional[str] = Field(default=None, alias="updatedAt")

    @field_validator("records", "charges", mode="before")
    @classmethod
    def _null_to_none(cls, v):
        # Go's JSON may serialise empty arrays as `null`; pydantic would
        # accept both, but be explicit so callers always see None.
        if v is None:
            return None
        return v


class ListPetsOutput(BaseModel):
    """Successful ``list_pets`` response. Mirrors the Go ``data`` envelope."""

    model_config = {"populate_by_name": True}

    items: list[Pet] = Field(default_factory=list, description="当前页的宠物档案数组。")
    total: int = Field(default=0, description="过滤后总条数。")
    page: int = Field(default=1, description="当前页码。")
    page_size: int = Field(
        default=20, alias="pageSize", description="每页条数。"
    )
    total_pages: int = Field(
        default=1, alias="totalPages", description="总页数。"
    )
    total_cost: float = Field(
        default=0.0, alias="totalCost", description="结果集所有宠物总花费合计。"
    )


# The wire shape the tool returns: success data OR the unified error
# envelope. The SDK surfaces whichever branch is returned through
# ``structured_content``, so MCP clients branch on the ``error`` field.
ListPetsResult = Union[ListPetsOutput, ErrorOutput]


# ---------------------------------------------------------------------------
# Tool implementation.
# ---------------------------------------------------------------------------
async def _list_pets_impl(
    arguments: dict[str, Any],
    client: PetHospitalClient,
) -> ListPetsResult:
    """Pure implementation — takes a dict, returns the union result.

    Decoupled from the MCP decorator so tests can call it directly with a
    respx-mocked :class:`PetHospitalClient` and check the wire shape
    without going through the SDK transport.
    """
    start = time.perf_counter()
    tool_name = "list_pets"
    # Sanitised copy for logging (owner_phone / ownerPhone masked).
    safe_args = sanitize(arguments)

    try:
        try:
            parsed = ListPetsInput.model_validate(arguments)
        except Exception as exc:
            # Validation failure: surface field-level details.
            details: dict[str, Any] = {}
            errors = getattr(exc, "errors", None)
            if callable(errors):
                errors = errors()
            if isinstance(errors, list):
                # Strip ``ctx`` — pydantic v2 puts the original ValueError
                # object there, which is not JSON-serialisable and would
                # break ``model_dump(mode="json")`` on ErrorOutput.
                details["validation_errors"] = [
                    {k: v for k, v in e.items() if k != "ctx"} for e in errors
                ]
            raise ToolError(
                ErrorCode.VALIDATION_ERROR,
                f"输入校验失败: {exc}",
                details=details,
            ) from exc

        params = parsed.to_camel_params()
        data = await client.list_pets(params)

        try:
            output = ListPetsOutput.model_validate(data)
        except Exception as exc:
            raise ToolError(
                ErrorCode.BACKEND_INVALID_RESPONSE,
                f"上游响应不符合数据模型: {exc}",
                details={"raw_data": str(data)[:512]},
            ) from exc

        duration_ms = int((time.perf_counter() - start) * 1000)
        _log.info(
            "tool call ok",
            tool_name=tool_name,
            params=safe_args,
            status="ok",
            duration_ms=duration_ms,
            total=output.total,
            page=output.page,
        )
        return output

    except ToolError as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        _log.warning(
            "tool call error",
            tool_name=tool_name,
            params=safe_args,
            status="error",
            duration_ms=duration_ms,
            error_code=exc.code.value,
            error_message=exc.message,
        )
        return exc.to_output()
    except Exception as exc:  # noqa: BLE001 — last-resort guard
        duration_ms = int((time.perf_counter() - start) * 1000)
        _log.error(
            "tool call internal error",
            tool_name=tool_name,
            params=safe_args,
            status="error",
            duration_ms=duration_ms,
            error=str(exc),
        )
        return ToolError(
            ErrorCode.INTERNAL_ERROR,
            f"内部错误: {exc}",
            details={},
        ).to_output()


def _build_input_schema() -> dict[str, Any]:
    """Build the JSON Schema advertised for the ``list_pets`` tool.

    The SDK's auto-generated schema (from the function signature) wraps
    every ``Optional`` field in ``anyOf`` and lacks flat ``enum`` /
    ``minimum`` / ``maximum`` keywords.  This hand-built schema is the
    *source of truth* for what :class:`ListPetsInput` actually enforces at
    runtime: 14 flat fields, enums for species/status/sort_by/order,
    numeric bounds for page/page_size, and ``additionalProperties: false``
    (unknown fields are rejected).
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "q": {"type": "string", "description": "全文检索关键词(跨字段,空格分词 AND)。"},
            "name": {"type": "string", "description": "宠物姓名模糊匹配。"},
            "owner_name": {"type": "string", "description": "主人姓名模糊匹配。"},
            "owner_phone": {"type": "string", "description": "主人电话模糊匹配(日志中脱敏)。"},
            "species": {
                "type": "string",
                "enum": SPECIES_VALUES,
                "description": f"宠物种类精确匹配。允许值: {', '.join(SPECIES_VALUES)}",
            },
            "doctor": {"type": "string", "description": "主治医生模糊匹配。"},
            "disease": {"type": "string", "description": "疾病/诊断模糊匹配。"},
            "status": {
                "type": "string",
                "enum": STATUS_VALUES,
                "description": f"就诊状态精确匹配。允许值: {', '.join(STATUS_VALUES)}",
            },
            "min": {"type": "number", "minimum": 0, "description": "最低总花费(含)。不得为负。"},
            "max": {"type": "number", "minimum": 0, "description": "最高总花费(含)。不得为负且 >= min。"},
            "sort_by": {
                "type": "string",
                "enum": SORT_BY_VALUES,
                "description": f"排序字段。允许值: {', '.join(SORT_BY_VALUES)}",
            },
            "order": {
                "type": "string",
                "enum": ORDER_VALUES,
                "description": "排序方向。允许值: asc, desc",
            },
            "page": {"type": "integer", "minimum": 1, "description": "页码,从 1 开始,默认 1。"},
            "page_size": {"type": "integer", "minimum": 1, "maximum": 500, "description": "每页条数,1-500,默认 20。"},
        },
    }


# Tool description — kept as a module constant so tests can assert on it.
_TOOL_DESCRIPTION = (
    "查询宠物医院档案列表,严格映射后端 GET /api/v1/pets。\n\n"
    "支持过滤(q/name/owner_name/owner_phone/species/doctor/disease/status/min/max)、"
    "排序(sort_by/order)、分页(page/page_size),全部可选。"
    "适合 AI agent 按主人电话/种类/疾病/状态/花费区间筛选并分页浏览宠物档案。\n\n"
    "参数:\n"
    "  q (str): 全文检索关键词\n"
    "  name (str): 宠物姓名模糊\n"
    "  owner_name (str): 主人姓名模糊\n"
    "  owner_phone (str): 主人电话模糊(日志脱敏)\n"
    "  species (str): 种类精确,枚举 犬/猫/兔/鸟/仓鼠/爬宠/其他\n"
    "  doctor (str): 医生模糊\n"
    "  disease (str): 疾病模糊\n"
    "  status (str): 状态精确,枚举 待就诊/就诊中/住院中/已康复/慢性病随访\n"
    "  min (float): 最低总花费(非负,有限)\n"
    "  max (float): 最高总花费(非负,有限,>= min)\n"
    "  sort_by (str): 排序字段 id/name/ownerName/species/doctor/disease/status/totalCost/visitCount/createdAt/updatedAt\n"
    "  order (str): 排序方向 asc/desc\n"
    "  page (int): 页码,>=1\n"
    "  page_size (int): 每页条数,1-500\n\n"
    "返回:成功时 {items,total,page,page_size,total_pages,total_cost};"
    "失败时 {error:{code,message,details}}。"
)


def register(mcp: MCPServer) -> None:
    """Register the ``list_pets`` tool on the given MCPServer.

    The tool constructs its own short-lived :class:`PetHospitalClient`
    per call so each invocation is fully independent (stateless HTTP
    friendly).

    Design notes
    ------------
    * The function signature uses permissive ``Optional`` types (no
      ``Literal`` / ``Field`` constraints).  This is deliberate: if the
      SDK's own arg-model rejected invalid enums *before* the function
      body runs, the error would surface as a bare ``ToolError`` (text
      content, no ``structured_content``).  By keeping the SDK layer
      permissive, *all* validation flows through :func:`_list_pets_impl`
      → :class:`ListPetsInput`, which returns a structured
      :class:`ErrorOutput` envelope for every failure.
    * The advertised JSON Schema is overridden post-registration with
      :func:`_build_input_schema` — a clean flat schema with enums,
      bounds, and ``additionalProperties: false`` that mirrors what
      ``ListPetsInput`` actually enforces.
    * The tool returns :class:`CallToolResult` directly (with
      ``structured_output=False``) so success and error results are
      surfaced as flat ``structuredContent`` without the SDK's
      ``{"result": ...}`` wrapping for ``Union`` returns.
    """

    @mcp.tool(
        name="list_pets",
        description=_TOOL_DESCRIPTION,
        structured_output=False,
    )
    async def list_pets(
        q: Optional[str] = None,
        name: Optional[str] = None,
        owner_name: Optional[str] = None,
        owner_phone: Optional[str] = None,
        species: Optional[str] = None,
        doctor: Optional[str] = None,
        disease: Optional[str] = None,
        status: Optional[str] = None,
        min: Optional[float] = None,  # noqa: A002 — matches the API param name
        max: Optional[float] = None,  # noqa: A002
        sort_by: Optional[str] = None,
        order: Optional[str] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> CallToolResult:
        """MCP entry point — see :func:`register` docstring above."""
        # Import here to avoid a circular import at module load time.
        from pet_hospital_mcp.config import get_settings

        # Rebuild the raw arguments dict, dropping None values so that
        # empty calls produce an empty dict (matching the Go API defaults).
        raw: dict[str, Any] = {
            "q": q,
            "name": name,
            "owner_name": owner_name,
            "owner_phone": owner_phone,
            "species": species,
            "doctor": doctor,
            "disease": disease,
            "status": status,
            "min": min,
            "max": max,
            "sort_by": sort_by,
            "order": order,
            "page": page,
            "page_size": page_size,
        }
        arguments = {k: v for k, v in raw.items() if v is not None}

        settings = get_settings()
        async with PetHospitalClient(settings) as client:
            result = await _list_pets_impl(arguments, client)

        sc = result.model_dump(mode="json", by_alias=True)
        is_error = isinstance(result, ErrorOutput)
        return CallToolResult(
            content=[
                TextContent(type="text", text=_json.dumps(sc, ensure_ascii=False))
            ],
            structured_content=sc,
            is_error=is_error,
        )

    # Override the advertised input schema.  The SDK auto-generates a
    # schema with ``anyOf`` wrappers for Optional fields and without
    # ``enum`` / ``minimum`` / ``maximum`` / ``additionalProperties: false``.
    # ``_build_input_schema()`` is the flat, exact mirror of what
    # ``ListPetsInput`` enforces at runtime.
    tool = mcp._tool_manager.get_tool("list_pets")  # type: ignore[attr-defined]
    if tool is not None:
        tool.parameters = _build_input_schema()
