# Boxzooka MCP Server

Wraps the Boxzooka bxz-api (Product, Inbound, Order, Inventory, Shipment,
Return) as an MCP server, same pattern as the Materials Inventory connector:
one shared server, one shared credential pair, the whole team connects to
the same URL.

33 tools, full read + write coverage. EDI is not covered — it's XML-based
and Boxzooka requires a separate setup conversation with their IT team for
it; ask if you actually need it and I'll add it.

## Files

- `boxzooka_mcp.py` — the server
- `requirements.txt` — Python deps
- `Dockerfile` — container build for remote hosting

## 1. Deploy it (separate service from Materials Inventory)

This is a separate deployment from the Materials Inventory connector —
each server needs its own Railway/Render/Fly service.

1. Push these three files to a new repo (or folder in an existing one).
2. Railway → New Project → Deploy from GitHub repo → pick it.
3. In **Variables**, add:
   - `BOXZOOKA_API_TOKEN` = your sandbox token
     (`c44f966f5c7fc4cdb7d7f461ab4414c6`)
   - `BOXZOOKA_CUSTOMER_ID` = `242`
   - `BOXZOOKA_API_BASE_URL` = `https://api.boxzooka.com` for **production**
     (live warehouse data). If omitted, defaults to the sandbox
     (`https://sandbox.boxzooka.com`), which is static test data and will
     NOT reflect live shipments — set the production URL for real numbers.
     Note: production may require a different token/customer ID than
     sandbox; if calls return 401 after switching, get live credentials
     from Boxzooka.
4. Railway gives you a public URL, e.g.
   `https://boxzooka-production-xxxx.up.railway.app`.
5. The connector URL to give your team is that URL **plus `/mcp`**:
   `https://boxzooka-production-xxxx.up.railway.app/mcp`

## 2. Connect the team

Same as Materials Inventory: each teammate adds it as a custom connector
in their own Claude app, pointing at the URL above. No per-user setup.

## 3. Security & sandbox notes

- This token/customer pair currently points at Boxzooka's **sandbox**
  environment (per their docs: "for test and debug purpose"). Data you
  see through it is test data, not your real warehouse — orders and
  inventory here won't reflect what's actually happening at Boxzooka's
  physical warehouse. Once you're ready to go live, contact Boxzooka for
  production credentials and set them via `BOXZOOKA_API_TOKEN` /
  `BOXZOOKA_CUSTOMER_ID` / `BOXZOOKA_API_BASE_URL`.
- Same access-level note as Materials Inventory: this connector can create
  orders, cancel orders, adjust returns, etc. — full read/write. Don't
  share the URL outside the team.

## 4. What's covered

- **Product**: create, get by SKU, update, list all, list (paginated)
- **Inbound**: create, get by PO number, search by date range, get
  received-by-date (real-time), update, paginated receiving-by-date
- **Order**: create, cancel, search (by order_id/order_key/external_id),
  update, load WMS orders by date
- **Inventory**: list all in-stock, get by SKU, today's adjustments,
  adjustments by date, putaway by date, plus paginated variants of each
- **Shipment**: get by order key, get by date range, paginated by-date
- **Return**: search, search by date, create, update, cancel

Not covered: EDI (XML-based, needs a separate Boxzooka IT conversation).
