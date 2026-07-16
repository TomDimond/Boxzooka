#!/usr/bin/env python3
"""
MCP Server for the Boxzooka bxz-api (fulfillment/3PL warehouse system).

Covers Product, Inbound, Order, Inventory, Shipment, and Return.
EDI is intentionally not covered — it is XML-based and requires a
separate integration conversation with Boxzooka IT.

Auth:
    Every request needs two headers: `token` (the API token) and
    `customer` (the client id). Set these via the BOXZOOKA_API_TOKEN and
    BOXZOOKA_CUSTOMER_ID environment variables on the server — the whole
    team shares this one server/credential pair, so individual users
    don't need their own tokens.

Base URL:
    Defaults to the sandbox (https://sandbox.boxzooka.com). Once Boxzooka
    provisions a live/production URL, override it with
    BOXZOOKA_API_BASE_URL without changing any code.
"""

import json
import os
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE_URL = os.environ.get("BOXZOOKA_API_BASE_URL", "https://sandbox.boxzooka.com")
API_TOKEN = os.environ.get("BOXZOOKA_API_TOKEN", "")
CUSTOMER_ID = os.environ.get("BOXZOOKA_CUSTOMER_ID", "")

# See note in materials_inventory_mcp.py: host must be 0.0.0.0 for remote
# hosting (Railway/Render/Fly), and DNS-rebinding protection must be
# disabled since the server is addressed by a public hostname that isn't
# known at container build time. The token/customer pair is the real
# access control here, not this transport-level check.
mcp = FastMCP(
    "boxzooka_mcp",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8000")),
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

SKU_PATTERN = r"^[A-Za-z0-9_-]+$"


def _auth_headers() -> Dict[str, str]:
    if not API_TOKEN or not CUSTOMER_ID:
        raise RuntimeError(
            "BOXZOOKA_API_TOKEN and/or BOXZOOKA_CUSTOMER_ID is not set on the "
            "server. Ask whoever deployed this MCP server to configure them."
        )
    return {"token": API_TOKEN, "customer": CUSTOMER_ID, "Content-Type": "application/json"}


async def _request(
    method: str,
    path: str,
    *,
    json_body: Optional[Any] = None,
) -> Any:
    """Shared request helper used by every tool."""
    url = f"{API_BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            method,
            url,
            headers=_auth_headers(),
            json=json_body,
        )
        response.raise_for_status()
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw_response": response.text}


def _handle_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        try:
            detail = e.response.json()
        except Exception:
            detail = e.response.text
        if status == 401:
            return "Error: Authentication failed (401). Check BOXZOOKA_API_TOKEN / BOXZOOKA_CUSTOMER_ID."
        if status == 403:
            return "Error: Permission denied (403) for this resource."
        if status == 404:
            return "Error: Resource not found (404). Check the ID/key is correct."
        if status == 422:
            return f"Error: Invalid request (422). Details: {detail}"
        if status == 429:
            return "Error: Rate limit exceeded (429). Wait a moment and retry."
        return f"Error: API request failed with status {status}. Details: {detail}"
    if isinstance(e, httpx.TimeoutException):
        return "Error: Request to Boxzooka API timed out. Please retry."
    if isinstance(e, RuntimeError):
        return f"Error: {e}"
    return f"Error: Unexpected error ({type(e).__name__}): {e}"


def _json(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


class ShippingMethod(str, Enum):
    PACK_AND_HOLD = "BXZ.PKP"
    BXZ_1_DAY = "BXZ.USA.1"
    BXZ_2_DAY = "BXZ.USA.2"
    BXZ_3_DAY = "BXZ.USA.3"
    BXZ_5_DAY = "BXZ.USA.5"
    BXZ_7_DAY = "BXZ.USA.7"
    UPS_NEXT_DAY_EARLY = "UPS.EXP.1"
    UPS_NEXT_DAY = "UPS.DOM.1"
    UPS_2ND_DAY = "UPS.DOM.2"
    UPS_3_DAY = "UPS.DOM.3"
    UPS_GROUND = "UPS.GRD.RESI"
    FEDEX_DOM_1 = "FDX.DOM.1"


class OrderType(str, Enum):
    RETAIL = "retail"
    DROPSHIP = "dropship"
    EDI = "edi"
    WHOLESALE = "whole-sale"
    GIFT = "gift"
    MONOGRAM = "monogram"
    FINAL_SALE = "final-sale"


class ReturnReason(str, Enum):
    NONE = "none"
    WRONG_SIZE = "wrong_size"
    CHANGED_MY_MIND = "changed_my_mind"
    CREATED_BY_MISTAKE = "created_by_mistake"
    EXCHANGE = "exchange"
    PRODUCT_FIT = "product_fit"
    NOT_MATCH = "not_match"
    STAINED_DAMAGED = "stained_damaged"
    CUSTOMER_CUTTAG = "customer_cuttag"
    DAMAGED_PACKAGING = "damaged_packaging"
    GIFT = "gift"
    RETURN_TO_SENDER = "return_to_sender"


class ProductCondition(str, Enum):
    GOOD = "GOOD"
    DAMAGED = "DAMAGED"
    CLEAN = "CLEAN"


# ===========================================================================
# Product
# ===========================================================================


class ProductInput(BaseModel):
    """Full set of Product properties, shared by create/update."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    sku: str = Field(
        ...,
        description="Unique product ID (numbers, letters, dash, underscore only). Cannot be changed once set.",
        pattern=SKU_PATTERN,
    )
    upc: Optional[str] = Field(default=None, description="Vendor barcode, sub-identifier for the product.")
    name: Optional[str] = Field(default=None, description="Product name.")
    retail_value: str = Field(..., description="Retail price, positive number as a string, e.g. '59.9'. USD, no currency symbol.")
    wholesale_value: Optional[str] = Field(default=None, description="Wholesale price, positive number as a string.")
    sale: Optional[str] = Field(default=None, description="Additional price field.")
    buy: Optional[str] = Field(default=None, description="Additional price field.")
    length: Optional[str] = Field(default=None, description="Product length, non-negative number.")
    width: Optional[str] = Field(default=None, description="Product width, non-negative number.")
    height: Optional[str] = Field(default=None, description="Product height, non-negative number.")
    weight: Optional[str] = Field(default=None, description="Product weight, non-negative number.")
    category: Optional[str] = Field(default=None, description="Product category.")
    brand: Optional[str] = Field(default=None, description="Product brand.")
    size: Optional[str] = Field(default=None, description="Size (e.g. S, M, L), usually for apparel.")
    color: Optional[str] = Field(default=None, description="Product color.")
    style: Optional[str] = Field(default=None, description="Product style.")
    description: Optional[str] = Field(default=None, description="Full product description.")
    short_description: Optional[str] = Field(
        default=None, description="Short description, max 255 characters.", max_length=255
    )
    sub_category: Optional[str] = Field(default=None, description="Product sub-category.")
    collection: Optional[str] = Field(default=None, description="Product collection.")
    image: Optional[str] = Field(default=None, description="Image URL for the product.")
    material: Optional[str] = Field(default=None, description="Product material.")
    hscode: Optional[str] = Field(default=None, description="Harmonized System (customs) code.")
    country: Optional[str] = Field(default=None, description="Country of origin, e.g. 'US'.")


@mcp.tool(
    name="boxzooka_create_product",
    annotations={
        "title": "Create Product",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def boxzooka_create_product(params: ProductInput) -> str:
    """Add a new product to the Boxzooka system.

    Args:
        params (ProductInput): sku and retail_value required, plus optional
            product attributes (see model fields).

    Returns:
        str: JSON 'true' on success, or "Error: ..." on failure (e.g. a
            duplicate SKU).
    """
    try:
        body = params.model_dump(exclude_none=True)
        data = await _request("POST", "/v2/product", json_body=body)
        return _json(data)
    except Exception as e:
        return _handle_error(e)


class GetProductBySkuInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    sku: str = Field(..., description="The unique product SKU to look up.")


@mcp.tool(
    name="boxzooka_get_product_by_sku",
    annotations={
        "title": "Get Product By SKU",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_get_product_by_sku(params: GetProductBySkuInput) -> str:
    """Get a single product's information by SKU.

    Args:
        params (GetProductBySkuInput): sku.

    Returns:
        str: JSON product object, or "Error: ..." on failure.
    """
    try:
        data = await _request("GET", f"/v2/product/{params.sku}")
        return _json(data)
    except Exception as e:
        return _handle_error(e)


class UpdateProductInput(ProductInput):
    """Same shape as ProductInput; sku identifies the product to update
    and is not itself changed."""


@mcp.tool(
    name="boxzooka_update_product",
    annotations={
        "title": "Update Product",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_update_product(params: UpdateProductInput) -> str:
    """Update product information in the system. The sku field identifies
    which product to update (it is not changed); all other fields you
    pass overwrite the current product data on file.

    Args:
        params (UpdateProductInput): sku (identifies the product) plus any
            fields to change.

    Returns:
        str: JSON 'true' on success, or "Error: ..." on failure.
    """
    try:
        body = params.model_dump(exclude_none=True)
        data = await _request("PUT", "/v2/product", json_body=body)
        return _json(data)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="boxzooka_list_products",
    annotations={
        "title": "List All Products",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_list_products() -> str:
    """Get all products under the current client. Not paginated — for
    large catalogs prefer boxzooka_list_products_paginated.

    Returns:
        str: JSON array of product objects, or "Error: ..." on failure.
    """
    try:
        data = await _request("GET", "/v2/product")
        return _json(data)
    except Exception as e:
        return _handle_error(e)


class PaginationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    page_size: int = Field(default=100, description="Number of results per page.", ge=1, le=500)
    page_number: int = Field(default=1, description="Page number to fetch (1-indexed).", ge=1)


@mcp.tool(
    name="boxzooka_list_products_paginated",
    annotations={
        "title": "List Products (Paginated)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_list_products_paginated(params: PaginationInput) -> str:
    """Get all products for the client, one page at a time. Prefer this
    over boxzooka_list_products when the catalog is large.

    Args:
        params (PaginationInput): page_size, page_number.

    Returns:
        str: JSON page of product objects, or "Error: ..." on failure.
    """
    try:
        body = params.model_dump()
        data = await _request("POST", "/v2/productPagination", json_body=body)
        return _json(data)
    except Exception as e:
        return _handle_error(e)


# ===========================================================================
# Inbound
# ===========================================================================


class InboundPOItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    sku: str = Field(..., description="Product SKU for this line item. Must be unique within the po_items array.")
    upc: Optional[str] = Field(default=None, description="Vendor barcode for this line item.")
    quantity: int = Field(..., description="Quantity of this item sent in the Inbound PO.", gt=0)
    external_id: Optional[str] = Field(default=None, description="Additional info passed through from third-party platforms.")
    external_line: Optional[str] = Field(default=None, description="Additional line-level info from third-party platforms.")


class CreateInboundInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    po_number: str = Field(
        ..., description="Unique Inbound PO ID (numbers, letters, dash, underscore only). Cannot be changed once set.",
        pattern=SKU_PATTERN,
    )
    warehouse_id: int = Field(..., description="ID of the warehouse this Inbound is sent to.")
    estimated_deliver_date: Optional[str] = Field(
        default=None, description="Estimated arrival date, format MM/DD/YYYY."
    )
    external_id: Optional[str] = Field(default=None, description="Additional info used by third-party platforms.")
    po_items: List[InboundPOItem] = Field(..., description="List of products and quantities being sent in this Inbound PO.", min_length=1)


@mcp.tool(
    name="boxzooka_create_inbound",
    annotations={
        "title": "Create Inbound PO",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def boxzooka_create_inbound(params: CreateInboundInput) -> str:
    """Create a new Inbound PO (restock shipment) in the system.

    Args:
        params (CreateInboundInput): po_number, warehouse_id, po_items
            required; estimated_deliver_date, external_id optional.

    Returns:
        str: JSON 'true' on success, or "Error: ..." on failure.
    """
    try:
        body = params.model_dump(exclude_none=True)
        data = await _request("POST", "/v2/inbound", json_body=body)
        return _json(data)
    except Exception as e:
        return _handle_error(e)


class GetInboundByPOInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    po_number: str = Field(..., description="The unique Inbound PO number to look up.")


@mcp.tool(
    name="boxzooka_get_inbound_by_po",
    annotations={
        "title": "Get Inbound By PO Number",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_get_inbound_by_po(params: GetInboundByPOInput) -> str:
    """Search for a single Inbound PO by its PO number.

    Args:
        params (GetInboundByPOInput): po_number.

    Returns:
        str: JSON Inbound object, or "Error: ..." on failure.
    """
    try:
        data = await _request("GET", f"/v2/inbound/{params.po_number}")
        return _json(data)
    except Exception as e:
        return _handle_error(e)


class DateRangeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    date_from: str = Field(..., description="Start date, format YYYY-MM-DD.")
    date_to: str = Field(..., description="End date, format YYYY-MM-DD.")


@mcp.tool(
    name="boxzooka_search_inbound_by_date",
    annotations={
        "title": "Search Inbound By Date Range",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_search_inbound_by_date(params: DateRangeInput) -> str:
    """Get Inbound POs with items received within a date range. Note this
    only includes items received within the range, and might not be the
    full Inbound item list for POs that span multiple receiving days.

    Args:
        params (DateRangeInput): date_from, date_to (YYYY-MM-DD).

    Returns:
        str: JSON array of Inbound PO objects, or "Error: ..." on failure.
    """
    try:
        data = await _request("GET", f"/v2/inbound/{params.date_from}/{params.date_to}")
        return _json(data)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="boxzooka_get_inbound_received_by_date",
    annotations={
        "title": "Get Inbound Received By Date (Real-Time)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_get_inbound_received_by_date(params: DateRangeInput) -> str:
    """Get real-time inbound receiving records within a date range.

    Args:
        params (DateRangeInput): date_from, date_to (YYYY-MM-DD).

    Returns:
        str: JSON receiving records, or "Error: ..." on failure.
    """
    try:
        data = await _request("GET", f"/v2/inboundrealtime/{params.date_from}/{params.date_to}")
        return _json(data)
    except Exception as e:
        return _handle_error(e)


class UpdateInboundInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    po_number: str = Field(..., description="The Inbound PO number to update. Identifies the record; not itself changed.")
    warehouse_id: Optional[int] = Field(default=None, description="ID of the warehouse this Inbound is sent to.")
    estimated_deliver_date: Optional[str] = Field(default=None, description="Estimated arrival date.")
    external_id: Optional[str] = Field(default=None, description="Additional info used by third-party platforms.")
    po_items: Optional[List[InboundPOItem]] = Field(default=None, description="Updated list of products/quantities for this PO.")


@mcp.tool(
    name="boxzooka_update_inbound",
    annotations={
        "title": "Update Inbound PO",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_update_inbound(params: UpdateInboundInput) -> str:
    """Update an existing Inbound PO.

    Args:
        params (UpdateInboundInput): po_number (required, identifies the
            PO) plus any fields to change.

    Returns:
        str: JSON 'true' on success, or "Error: ..." on failure.
    """
    try:
        body = params.model_dump(exclude_none=True)
        data = await _request("PUT", "/v2/inbound", json_body=body)
        return _json(data)
    except Exception as e:
        return _handle_error(e)


class InboundReceivingPaginationInput(PaginationInput):
    from_date: str = Field(..., description="Start date, format YYYY-MM-DD.")
    to_date: str = Field(..., description="End date, format YYYY-MM-DD.")


@mcp.tool(
    name="boxzooka_inbound_receiving_by_date_paginated",
    annotations={
        "title": "Inbound Receiving By Date (Paginated)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_inbound_receiving_by_date_paginated(params: InboundReceivingPaginationInput) -> str:
    """Get real-time inbound receiving records within a date range, one
    page at a time. Prefer this over boxzooka_get_inbound_received_by_date
    when the result set is large.

    Args:
        params (InboundReceivingPaginationInput): page_size, page_number,
            from_date, to_date.

    Returns:
        str: JSON page of receiving records, or "Error: ..." on failure.
    """
    try:
        body = params.model_dump()
        data = await _request("POST", "/v2/inboundrealtimePagination", json_body=body)
        return _json(data)
    except Exception as e:
        return _handle_error(e)


# ===========================================================================
# Order
# ===========================================================================


class OrderAddress(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    first_name: str = Field(..., description="Shipping address first name.")
    last_name: str = Field(..., description="Shipping address last name.")
    company: Optional[str] = Field(default=None, description="Shipping address company.")
    address1: str = Field(..., description="Shipping address line 1.")
    address2: Optional[str] = Field(default=None, description="Shipping address line 2.")
    city: str = Field(..., description="Shipping address city.")
    province: Optional[str] = Field(
        default=None, description="State/province. Required for US domestic orders; use the 2-letter code."
    )
    zip: str = Field(..., description="Shipping address zip/postal code.")
    country: str = Field(..., description="Shipping address country code, e.g. 'US'.")
    phone: Optional[str] = Field(default=None, description="Phone number. Required for international orders.")
    email: Optional[str] = Field(default=None, description="Email address. Required for international orders.")


class OrderItemInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    sku: str = Field(..., description="Product SKU. Must already exist in the system, and be unique within this order's item list.")
    name: Optional[str] = Field(default=None, description="Product name.")
    weight: Optional[float] = Field(default=None, description="Product weight, non-negative.", ge=0)
    quantity: int = Field(..., description="Quantity of this product in the order.", gt=0)
    value: float = Field(..., description="Price for a single unit of this product (not multiplied by quantity).", ge=0)
    other: Optional[Dict[str, Any]] = Field(default=None, description="Additional optional info for this order product.")


class CreateOrderInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    order_key: str = Field(
        ..., description="Unique order ID (numbers, letters, dash, underscore only), max 25 characters. Cannot be changed once set.",
        max_length=25, pattern=SKU_PATTERN,
    )
    external_id: Optional[str] = Field(default=None, description="Order identifier from your own/third-party system.")
    package_id: Optional[str] = Field(default=None, description="Additional id used to mark the order or package.")
    warehouse_id: int = Field(..., description="ID of the warehouse that will fulfill the order.")
    method: ShippingMethod = Field(..., description="Shipping method code for the order.")
    carrier_account: Optional[str] = Field(
        default=None, description="Your own carrier account number, if not using Boxzooka's default carrier account."
    )
    vendor: Optional[str] = Field(default=None, description="Vendor for drop-ship orders.")
    store: Optional[str] = Field(default=None, description="Store, used for EDI orders.")
    dc: Optional[str] = Field(default=None, description="DC, used for EDI orders.")
    department: Optional[str] = Field(default=None, description="Department, used for EDI orders.")
    slip_note: Optional[str] = Field(default=None, description="Special instructions/message for the order.")
    start_date: Optional[str] = Field(default=None, description="Date the order should start processing.")
    ship_date: Optional[str] = Field(default=None, description="Date the order should be shipped.")
    cancel_date: Optional[str] = Field(
        default=None, description="Date after which the order should be canceled if it hasn't shipped."
    )
    order_value: Optional[float] = Field(
        default=None, description="Total value of the order's products. Optional domestically; required for international orders.", ge=0
    )
    order_type: OrderType = Field(..., description="Type of order.")
    address: OrderAddress = Field(..., description="Shipping address for the order.")
    item: List[OrderItemInput] = Field(..., description="List of products in the order.", min_length=1)
    other: Optional[Dict[str, Any]] = Field(default=None, description="Additional optional order-level info.")


@mcp.tool(
    name="boxzooka_create_order",
    annotations={
        "title": "Create Order",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def boxzooka_create_order(params: CreateOrderInput) -> str:
    """Add a new order into the Boxzooka system for fulfillment.

    Args:
        params (CreateOrderInput): order_key, warehouse_id, method,
            order_type, address, item required; many optional fields (see
            model). Note: item quantities cannot be changed once the order
            starts processing.

    Returns:
        str: JSON success message on success, or "Error: ..." on failure.
    """
    try:
        body = params.model_dump(exclude_none=True, mode="json")
        data = await _request("POST", "/v2/order", json_body=body)
        return _json(data)
    except Exception as e:
        return _handle_error(e)


class CancelOrderInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    order_key: str = Field(..., description="The order_key of the order to cancel.")


@mcp.tool(
    name="boxzooka_cancel_order",
    annotations={
        "title": "Cancel Order",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_cancel_order(params: CancelOrderInput) -> str:
    """Cancel an order by its order_key. The order still exists afterward
    with status 'canceled' and will not be processed further. Orders that
    have already shipped cannot be canceled.

    Args:
        params (CancelOrderInput): order_key.

    Returns:
        str: JSON success message on success, or "Error: ..." on failure
            (e.g. if the order already shipped).
    """
    try:
        data = await _request("DELETE", f"/v2/order/{params.order_key}")
        return _json(data)
    except Exception as e:
        return _handle_error(e)


class SearchOrderType(str, Enum):
    ORDER_ID = "order_id"
    ORDER_KEY = "order_key"
    EXTERNAL_ID = "external_id"


class SearchOrderInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    search_type: SearchOrderType = Field(..., description="What kind of identifier 'keyword' is.")
    keyword: str = Field(..., description="The order_id, order_key, or external_id value to search for.")


@mcp.tool(
    name="boxzooka_search_order",
    annotations={
        "title": "Search Order",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_search_order(params: SearchOrderInput) -> str:
    """Look up order information by order_id, order_key, or external_id.

    Args:
        params (SearchOrderInput): search_type, keyword.

    Returns:
        str: JSON order object, or "Error: ..." on failure.
    """
    try:
        data = await _request("GET", f"/v2/order/{params.search_type.value}/{params.keyword}")
        return _json(data)
    except Exception as e:
        return _handle_error(e)


class UpdateOrderInput(BaseModel):
    """Same shape as CreateOrderInput but order_key identifies the order
    to update and every other field is optional."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    order_key: str = Field(..., description="The order_key identifying which order to update.")
    external_id: Optional[str] = Field(default=None, description="Order identifier from your own/third-party system.")
    package_id: Optional[str] = Field(default=None, description="Additional id used to mark the order or package.")
    warehouse_id: Optional[int] = Field(default=None, description="ID of the warehouse that will fulfill the order.")
    method: Optional[ShippingMethod] = Field(default=None, description="Shipping method code for the order.")
    carrier_account: Optional[str] = Field(default=None, description="Your own carrier account number.")
    vendor: Optional[str] = Field(default=None, description="Vendor for drop-ship orders.")
    store: Optional[str] = Field(default=None, description="Store, used for EDI orders.")
    dc: Optional[str] = Field(default=None, description="DC, used for EDI orders.")
    department: Optional[str] = Field(default=None, description="Department, used for EDI orders.")
    slip_note: Optional[str] = Field(default=None, description="Special instructions/message for the order.")
    start_date: Optional[str] = Field(default=None, description="Date the order should start processing.")
    ship_date: Optional[str] = Field(default=None, description="Date the order should be shipped.")
    cancel_date: Optional[str] = Field(default=None, description="Date after which the order should be canceled.")
    order_value: Optional[float] = Field(default=None, description="Total value of the order's products.", ge=0)
    order_type: Optional[OrderType] = Field(default=None, description="Type of order.")
    address: Optional[OrderAddress] = Field(default=None, description="Shipping address for the order.")
    item: Optional[List[OrderItemInput]] = Field(default=None, description="Updated list of products in the order.")
    other: Optional[Dict[str, Any]] = Field(default=None, description="Additional optional order-level info.")


@mcp.tool(
    name="boxzooka_update_order",
    annotations={
        "title": "Update Order",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_update_order(params: UpdateOrderInput) -> str:
    """Update an existing order that hasn't shipped yet.

    Args:
        params (UpdateOrderInput): order_key (required, identifies the
            order) plus any fields to change.

    Returns:
        str: JSON order object on success, or "Error: ..." on failure.
    """
    try:
        body = params.model_dump(exclude_none=True, mode="json")
        data = await _request("PUT", "/v2/order", json_body=body)
        return _json(data)
    except Exception as e:
        return _handle_error(e)


class LoadWmsOrderByDateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    from_date: str = Field(..., description="Start date, format YYYY-MM-DD.")
    to_date: str = Field(..., description="End date, format YYYY-MM-DD.")
    warehouse_id: int = Field(..., description="ID of the warehouse to load orders for.")


@mcp.tool(
    name="boxzooka_load_wms_order_by_date",
    annotations={
        "title": "Load WMS Orders By Date",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_load_wms_order_by_date(params: LoadWmsOrderByDateInput) -> str:
    """Load orders processed in the Boxzooka WMS within a date range for a
    given warehouse.

    Args:
        params (LoadWmsOrderByDateInput): from_date, to_date, warehouse_id.

    Returns:
        str: JSON array of order objects, or "Error: ..." on failure.
    """
    try:
        body = params.model_dump()
        data = await _request("POST", "/v2/wmsOrderByDate", json_body=body)
        return _json(data)
    except Exception as e:
        return _handle_error(e)


# ===========================================================================
# Inventory
# ===========================================================================


@mcp.tool(
    name="boxzooka_list_inventory",
    annotations={
        "title": "List All Inventory",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_list_inventory() -> str:
    """List all in-stock inventory. Out-of-stock products are not shown by
    this endpoint. For large catalogs prefer
    boxzooka_list_inventory_paginated.

    Returns:
        str: JSON inventory object, or "Error: ..." on failure.
    """
    try:
        data = await _request("GET", "/v2/inventory")
        return _json(data)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="boxzooka_get_inventory_by_sku",
    annotations={
        "title": "Get Inventory By SKU",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_get_inventory_by_sku(params: GetProductBySkuInput) -> str:
    """Get inventory for a single product by SKU.

    Args:
        params (GetProductBySkuInput): sku.

    Returns:
        str: JSON inventory object for that SKU, or "Error: ..." on failure.
    """
    try:
        data = await _request("GET", f"/v2/inventory/{params.sku}")
        return _json(data)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="boxzooka_get_adjustment_today",
    annotations={
        "title": "Get Today's Adjustment Records",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_get_adjustment_today() -> str:
    """Get inventory adjustment records from the start of today to now,
    grouped by warehouse.

    Returns:
        str: JSON adjustments object, or "Error: ..." on failure.
    """
    try:
        data = await _request("GET", "/v2/adjustment")
        return _json(data)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="boxzooka_get_adjustment_by_date",
    annotations={
        "title": "Get Adjustment History By Date",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_get_adjustment_by_date(params: DateRangeInput) -> str:
    """Get inventory adjustment history within a date range (inclusive),
    grouped by warehouse.

    Args:
        params (DateRangeInput): date_from, date_to (YYYY-MM-DD).

    Returns:
        str: JSON adjustments object, or "Error: ..." on failure.
    """
    try:
        data = await _request("GET", f"/v2/adjustment/{params.date_from}/{params.date_to}")
        return _json(data)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="boxzooka_get_putaway_by_date",
    annotations={
        "title": "Get Putaway By Date",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_get_putaway_by_date(params: DateRangeInput) -> str:
    """Get inventory putaway history within a date range (inclusive),
    grouped by warehouse. Putaway is when products move from a dock
    location to a pickable location.

    Args:
        params (DateRangeInput): date_from, date_to (YYYY-MM-DD).

    Returns:
        str: JSON putaway object, or "Error: ..." on failure.
    """
    try:
        data = await _request("GET", f"/v2/putaway/{params.date_from}/{params.date_to}")
        return _json(data)
    except Exception as e:
        return _handle_error(e)


class InventoryPaginationInput(PaginationInput):
    warehouse_id: Optional[int] = Field(default=None, description="Filter to a specific warehouse.")


@mcp.tool(
    name="boxzooka_list_inventory_paginated",
    annotations={
        "title": "List Inventory (Paginated)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_list_inventory_paginated(params: InventoryPaginationInput) -> str:
    """List in-stock inventory, one page at a time.

    Args:
        params (InventoryPaginationInput): page_size, page_number,
            warehouse_id (optional).

    Returns:
        str: JSON page of inventory, or "Error: ..." on failure.
    """
    try:
        body = params.model_dump(exclude_none=True)
        data = await _request("POST", "/v2/inventoryPagination", json_body=body)
        return _json(data)
    except Exception as e:
        return _handle_error(e)


class DatePaginationInput(PaginationInput):
    from_date: str = Field(..., description="Start date, format YYYY-MM-DD.")
    to_date: str = Field(..., description="End date, format YYYY-MM-DD.")
    warehouse_id: Optional[int] = Field(default=None, description="Filter to a specific warehouse.")


@mcp.tool(
    name="boxzooka_putaway_by_date_paginated",
    annotations={
        "title": "Putaway By Date (Paginated)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_putaway_by_date_paginated(params: DatePaginationInput) -> str:
    """Get inventory putaway history within a date range, one page at a
    time. Prefer this over boxzooka_get_putaway_by_date for large result
    sets.

    Args:
        params (DatePaginationInput): page_size, page_number, from_date,
            to_date, warehouse_id (optional).

    Returns:
        str: JSON page of putaway records, or "Error: ..." on failure.
    """
    try:
        body = params.model_dump(exclude_none=True)
        data = await _request("POST", "/v2/putawayByDatePagination", json_body=body)
        return _json(data)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="boxzooka_adjustment_by_date_paginated",
    annotations={
        "title": "Adjustment By Date (Paginated)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_adjustment_by_date_paginated(params: DatePaginationInput) -> str:
    """Get inventory adjustment history within a date range, one page at a
    time. Prefer this over boxzooka_get_adjustment_by_date for large
    result sets.

    Args:
        params (DatePaginationInput): page_size, page_number, from_date,
            to_date, warehouse_id (optional).

    Returns:
        str: JSON page of adjustment records, or "Error: ..." on failure.
    """
    try:
        body = params.model_dump(exclude_none=True)
        data = await _request("POST", "/v2/adjustmentByDatePagination", json_body=body)
        return _json(data)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="boxzooka_adjustment_today_paginated",
    annotations={
        "title": "Today's Adjustments (Paginated)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_adjustment_today_paginated(params: InventoryPaginationInput) -> str:
    """Get today's inventory adjustment records, one page at a time.
    Prefer this over boxzooka_get_adjustment_today for large result sets.

    Args:
        params (InventoryPaginationInput): page_size, page_number,
            warehouse_id (optional).

    Returns:
        str: JSON page of today's adjustment records, or "Error: ..." on
            failure.
    """
    try:
        body = params.model_dump(exclude_none=True)
        data = await _request("POST", "/v2/adjustmentTodayPagination", json_body=body)
        return _json(data)
    except Exception as e:
        return _handle_error(e)


# ===========================================================================
# Shipment
# ===========================================================================


@mcp.tool(
    name="boxzooka_get_shipment_by_order_key",
    annotations={
        "title": "Get Shipment By Order Key",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_get_shipment_by_order_key(params: CancelOrderInput) -> str:
    """Get shipment/tracking information for a single order by order_key.

    Args:
        params (CancelOrderInput): order_key.

    Returns:
        str: JSON shipment object, or "Error: ..." on failure.
    """
    try:
        data = await _request("GET", f"/v2/shipment/{params.order_key}")
        return _json(data)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="boxzooka_get_shipment_by_date",
    annotations={
        "title": "Get Shipments By Date",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_get_shipment_by_date(params: DateRangeInput) -> str:
    """List shipments for all orders shipped within a date range.

    Args:
        params (DateRangeInput): date_from, date_to (YYYY-MM-DD).

    Returns:
        str: JSON object keyed by order_key with shipment details for
            each, or "Error: ..." on failure.
    """
    try:
        data = await _request("GET", f"/v2/shipment/{params.date_from}/{params.date_to}")
        return _json(data)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="boxzooka_shipment_by_date_paginated",
    annotations={
        "title": "Shipments By Date (Paginated)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_shipment_by_date_paginated(params: DatePaginationInput) -> str:
    """List shipments within a date range, one page at a time. Prefer this
    over boxzooka_get_shipment_by_date for large result sets.

    Args:
        params (DatePaginationInput): page_size, page_number, from_date,
            to_date, warehouse_id (optional).

    Returns:
        str: JSON page of shipment records, or "Error: ..." on failure.
    """
    try:
        body = params.model_dump(exclude_none=True)
        data = await _request("POST", "/v2/shipmentByDatePagination", json_body=body)
        return _json(data)
    except Exception as e:
        return _handle_error(e)


# ===========================================================================
# Return
# ===========================================================================


class SearchReturnType(str, Enum):
    ORDER_ID = "order_id"
    EXTERNAL_ID = "external_id"
    THIRD_PARTY_ID = "third_party_id"


class SearchReturnInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    search_type: SearchReturnType = Field(..., description="What kind of identifier 'keyword' is.")
    keyword: str = Field(..., description="The order_id, external_id, or third_party_id value to search for.")


@mcp.tool(
    name="boxzooka_search_return",
    annotations={
        "title": "Search Return",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_search_return(params: SearchReturnInput) -> str:
    """Look up return information for a single order.

    Args:
        params (SearchReturnInput): search_type, keyword.

    Returns:
        str: JSON return object, or "Error: ..." on failure.
    """
    try:
        data = await _request("GET", f"/v2/return/{params.search_type.value}/{params.keyword}")
        return _json(data)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="boxzooka_search_return_by_date",
    annotations={
        "title": "Search Returns By Date",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_search_return_by_date(params: DateRangeInput) -> str:
    """Get returns processed within a date range, across multiple orders.

    Args:
        params (DateRangeInput): date_from, date_to (YYYY-MM-DD).

    Returns:
        str: JSON return object(s), or "Error: ..." on failure.
    """
    try:
        data = await _request("GET", f"/v2/returnbydate/{params.date_from}/{params.date_to}")
        return _json(data)
    except Exception as e:
        return _handle_error(e)


class ReturnUnitInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    returnUnitId: Optional[str] = Field(default=None, description="Return unit identifier.")
    returnLineId: str = Field(..., description="Return line identifier (required, unique within this return).")
    sku: str = Field(..., description="SKU of the returned item. Must be on the original order's shipped SKU list.")
    upc: Optional[str] = Field(default=None, description="UPC for this item.")
    quantity: str = Field(..., description="Quantity of items in this return.")
    restockItem: Optional[bool] = Field(
        default=None,
        description="Whether this item will be restocked. If true, condition is set to GOOD; if false, DAMAGED.",
    )
    returnCondition: Optional[ProductCondition] = Field(
        default=None, description="Condition of the returned item, if known up front."
    )
    returnReason: Optional[ReturnReason] = Field(default=None, description="Reason code for the return.")


class CreateReturnInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    returnId: str = Field(
        ..., description="Unique return ID (numbers, letters, dash, underscore only). Used later to update/cancel this return.",
        pattern=SKU_PATTERN,
    )
    orderId: str = Field(
        ...,
        description="ID of the order being returned. If it wasn't shipped from Boxzooka, an unplanned order with this ID is created first.",
    )
    trackingNumber: Optional[str] = Field(default=None, description="Tracking number of the return shipment.")
    carrier: Optional[str] = Field(default=None, description="Carrier of the return shipment.")
    shipDate: Optional[str] = Field(
        default=None, description="ISO8601 date the return was/will be shipped, e.g. '2024-01-20T00:00:00'."
    )
    status: Optional[str] = Field(default=None, description="Status of the return.")
    returnUnits: List[ReturnUnitInput] = Field(..., description="List of items being returned.", min_length=1)


@mcp.tool(
    name="boxzooka_create_return",
    annotations={
        "title": "Create Return",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def boxzooka_create_return(params: CreateReturnInput) -> str:
    """Create a new return in the WMS for a shipped (or unplanned) order.

    Args:
        params (CreateReturnInput): returnId, orderId, returnUnits
            required; trackingNumber, carrier, shipDate, status optional.

    Returns:
        str: JSON return object on success, or "Error: ..." on failure.
    """
    try:
        body = params.model_dump(exclude_none=True, mode="json")
        data = await _request("POST", "/v2/return", json_body=body)
        return _json(data)
    except Exception as e:
        return _handle_error(e)


class UpdateReturnInput(CreateReturnInput):
    """Same shape as CreateReturnInput; returnId in the URL path
    identifies which return is updated."""


@mcp.tool(
    name="boxzooka_update_return",
    annotations={
        "title": "Update Return",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_update_return(params: UpdateReturnInput) -> str:
    """Update an existing return.

    Args:
        params (UpdateReturnInput): returnId (identifies the return, used
            in the URL) plus the rest of the return fields to set.

    Returns:
        str: JSON return object on success, or "Error: ..." on failure.
    """
    try:
        body = params.model_dump(exclude_none=True, mode="json")
        data = await _request("PUT", f"/v2/return/{params.returnId}", json_body=body)
        return _json(data)
    except Exception as e:
        return _handle_error(e)


class CancelReturnInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    return_id: str = Field(..., description="The returnId of the return to cancel.")


@mcp.tool(
    name="boxzooka_cancel_return",
    annotations={
        "title": "Cancel Return",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def boxzooka_cancel_return(params: CancelReturnInput) -> str:
    """Cancel an existing return.

    Args:
        params (CancelReturnInput): return_id.

    Returns:
        str: JSON confirmation on success, or "Error: ..." on failure.
    """
    try:
        data = await _request("PATCH", f"/v2/return/{params.return_id}")
        return _json(data)
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "streamable_http":
        # Underlying SDK's run() takes "streamable-http" (hyphen); host/
        # port/security are already configured on the FastMCP instance.
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
