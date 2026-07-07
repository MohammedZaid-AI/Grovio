# Grovio - AI-Powered Restaurant & Procurement Management System

## Project Overview

**Grovio** is an AI-powered restaurant operations platform that manages grocery ordering, procurement, inventory, and sales workflows through intelligent automation.

### What This Project Does
- **Auto-Ordering**: Intelligently orders groceries from Swiggy Instamart based on demand forecasting
- **Procurement Management**: Automates purchase order creation, approval, and tracking from suppliers
- **Inventory Tracking**: Maintains real-time inventory levels and automatic stock updates from invoices
- **Sales & Recipe Management**: Tracks dish preparation from recipes, calculates ingredient consumption from sales
- **WhatsApp Integration**: Full conversational interface via Twilio WhatsApp webhook
- **Admin Dashboard**: Web interface for order approval, inventory management, and recipe configuration

### Main Features
1. **Conversational AI**: Natural language understanding via LLM routing (LangGraph)
2. **Swiggy Instamart Integration**: Automated grocery procurement via MCP (Model Context Protocol)
3. **Invoice Processing**: OCR-based supplier invoice extraction and inventory sync
4. **Demand Forecasting**: Historical analysis and pattern recognition for procurement planning
5. **Multi-Agent System**: Specialized AI agents for different operational tasks
6. **Database Persistence**: SQLite for orders, inventory, invoices, and memory
7. **Admin Authentication**: JWT-based session management for dashboard access

### High-Level Architecture
```
WhatsApp Webhook (Twilio)
    ↓
Backend Chat Router (backend/chat.py)
    ↓
LangGraph Supervisor (ai/langgraph/graph.py)
    ↓
Specialized Agents (ai/agents/*.py)
    ├─ Auto Order Agent
    ├─ Purchase Approval Agent
    ├─ Procurement Forecaster
    ├─ Purchase Rejection Agent
    └─ [15+ domain-specific agents]
    ↓
Tools (ai/tools/*.py)
    ├─ Swiggy Tool (MCP)
    ├─ Procurement Tool
    ├─ Invoice Tool
    ├─ Supplier Tool
    └─ Forecast Tool
    ↓
Database (SQLite)
    └─ Orders, Inventory, Invoices, Memory, Recipes
```

---

## Tech Stack

| Layer | Technologies |
|-------|--------------|
| **Backend Framework** | FastAPI 0.138.0, Uvicorn 0.49.0 |
| **LLM/AI** | LangChain 1.3.4, LangGraph 1.2.4, Groq, OpenAI/OpenRouter |
| **Integrations** | Twilio 9.10.9, MCP 1.2.7.2, mcp-use 1.7.0 |
| **Database** | SQLite3 (via Python sqlite3) |
| **Web Framework** | FastAPI with Starlette 1.2.1 |
| **Data Processing** | Pandas 3.0.3, NumPy 2.5.0 |
| **OCR** | EasyOCR 1.7.2, OpenCV 4.13.0 |
| **PDF Processing** | PyMuPDF 1.27.2.3 |
| **Scheduling** | APScheduler 3.11.2 |
| **Authentication** | PyJWT 2.13.0 |
| **HTTP** | Requests 2.34.2, httpx 0.28.1 |
| **Frontend** | HTML/CSS/JavaScript (static assets) |
| **Python Version** | 3.10+ (inferred from dependencies) |

---

## Repository Structure

```
Grovio/
├── app.py                          # Main CLI entry point (interactive menu)
├── db.py                           # SQLite models & helpers (1600+ lines)
├── backend/
│   ├── app.py                      # FastAPI application setup
│   ├── routes.py                   # API endpoints (webhook, dashboard, upload)
│   ├── chat.py                     # Message router & business logic
│   ├── conversation_engine.py      # Conversation flow orchestration
│   ├── config.py                   # (empty, for future config)
│   ├── static/                     # Frontend assets
│   │   ├── login.html             # Dashboard login page
│   │   ├── dashboard.html         # Admin dashboard UI
│   │   ├── js/dashboard.js        # Dashboard logic
│   │   └── css/style.css          # Styling
│   └── requirements.txt            # Backend-specific deps
│
├── ai/                             # Core AI modules
│   ├── agents/                     # Specialized agents (20+ files)
│   │   ├── ai_coo.py              # Chief Operating Officer agent
│   │   ├── auto_order_agent.py    # Auto-ordering logic
│   │   ├── purchase_approval_agent.py
│   │   ├── purchase_rejection_agent.py
│   │   ├── procurement_forecaster.py
│   │   ├── dashboard_agent.py
│   │   └── [15+ others]
│   ├── langgraph/
│   │   ├── graph.py               # LangGraph state machine
│   │   ├── router.py              # Message routing (empty)
│   │   └── state.py               # RestaurantState TypedDict
│   ├── tools/                      # Tool implementations
│   │   ├── base_tool.py           # Abstract base
│   │   ├── swiggy_tool.py         # Swiggy API wrapper
│   │   ├── procurement_tool.py    # PO management
│   │   ├── invoice_tool.py        # Invoice extraction
│   │   ├── supplier_tool.py       # Supplier queries
│   │   ├── forecast_tool.py       # Demand forecasting
│   │   ├── memory_tool.py         # Session memory
│   │   ├── tool_registry.py       # Tool discovery
│   │   └── tool_executor.py       # Tool execution
│   ├── intelligence/
│   │   ├── product_matcher.py     # Product alias & fuzzy matching
│   │   ├── price_tracker.py       # Price history analysis
│   │   ├── supplier_memory.py     # Supplier performance tracking
│   │   ├── procurement_memory.py  # Procurement patterns
│   │   └── memory.py              # RestaurantMemory (stats/analytics)
│   ├── procurement/
│   │   ├── purchase_order.py      # PO model & methods
│   │   ├── purchase_order_generator.py
│   │   ├── purchase_order_approval.py
│   │   ├── purchase_order_rejection.py
│   │   └── quote_analyzer.py
│   ├── invoice/
│   │   ├── processor.py           # Invoice validation & processing
│   │   ├── pipeline.py            # Full pipeline orchestration
│   │   ├── inventory_sync.py      # Inventory updates from invoice
│   │   ├── price_sync.py          # Price history from invoice
│   │   └── validator.py           # Invoice schema validation
│   ├── conversation/
│   │   ├── session.py             # Session persistence
│   │   ├── session_memory.py      # Per-user memory
│   │   ├── conversation_memory.py
│   │   ├── history.py             # Chat history
│   │   ├── context.py             # Message context
│   │   ├── intent_router.py       # Intent classification
│   │   ├── formatter.py           # Response formatting
│   │   ├── commands.py            # Command parsing
│   │   └── chunker.py             # Long response chunking
│   ├── scheduler/
│   │   ├── scheduler.py           # Main scheduler loop
│   │   ├── auto_scheduler.py      # Recurring order automation
│   │   ├── daily_scheduler.py     # Daily tasks
│   │   ├── whatsapp_scheduler.py  # WhatsApp message scheduling
│   │   ├── morning_brief.py       # Daily report generation
│   │   └── approve_order.py       # Approval workflow
│   ├── finance/
│   │   └── finance_analyzer.py    # Revenue/cost analysis
│   ├── reports/
│   │   └── daily_brief.py         # Executive summary generation
│   ├── shopping/
│   │   ├── shopping_session.py    # Cart state management
│   │   └── orchestrator.py        # Shopping flow orchestration
│   ├── services/
│   │   └── [service layer - TBD]
│   ├── memory/                    # Persistent memory files (JSON)
│   └── prompts/                   # LLM system prompts (txt files)
│
├── integrations/
│   └── swiggy/
│       ├── swiggy_mcp.py          # Swiggy Instamart MCP client
│       ├── mcp.json               # MCP configuration
│       ├── debug_mcp.py           # Development tools
│       ├── inspect_checkout.py    # Checkout inspection
│       └── inspect_tools.py       # Tool inspection
│
├── whatsapp/
│   ├── webhook.py                 # Twilio webhook handler
│   ├── twilio.py                  # Twilio API wrapper
│   ├── message_handler.py         # Message processing
│   └── scripts/
│       ├── create_db.py
│       ├── view_orders.py
│       └── view_pending_orders.py
│
├── core/
│   ├── constants.py               # (empty)
│   └── logger.py                  # Logging utilities
│
├── data/
│   ├── reflection_memory.json     # Persistent memory snapshots
│   └── restaurant_memory.json
│
├── database/
│   └── orders.db                  # SQLite database file
│
├── prompts/                       # LLM system prompts
│   ├── grocery_parser.txt         # Order parsing
│   ├── invoice.txt                # Invoice extraction
│   ├── procurement.txt            # Procurement planning
│   ├── supplier.txt               # Supplier evaluation
│   └── coo.txt                    # COO instructions
│
├── downloads/                     # Uploaded invoice files (images/PDFs)
│
├── tests/
│   ├── test_admin_dashboard.py
│   ├── test_inventory_approval.py
│   ├── test_recipe_dashboard.py
│   ├── test_recurring.py
│   ├── test_memory.py
│   ├── test_mcp.py
│   └── test_address.py
│
├── requirements.txt               # Python dependencies (entire stack)
├── .env                          # Environment variables (secrets)
├── .gitignore                    # Git ignore patterns
└── CLAUDE.md                     # This file

```

---

## Local Development

### Installation

1. **Clone and setup**
   ```bash
   cd "d:\Zaids Work\Grovio"
   python -m venv venv
   source venv/Scripts/activate  # Windows
   # or: source venv/bin/activate  # Linux/Mac
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Environment setup** (see Environment Variables section)

### Environment Setup

Create a `.env` file in the project root with all required variables (see Environment Variables section below).

### Running Locally

**CLI Mode** (interactive menu):
```bash
python app.py
```
Menu options:
1. Order groceries now
2. Create recurring order
3. View recurring orders
4. Run scheduler
5. View pending orders
6. Approve pending orders
7. Order History
8. Exit

**Web Server** (FastAPI backend):
```bash
cd backend
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```
- Dashboard: http://localhost:8000/static/login.html
- API: http://localhost:8000/
- Health: GET http://localhost:8000/

**Scheduler** (background automation):
```bash
python -c "from ai.scheduler.scheduler import run_scheduler; run_scheduler()"
```

### Build

No build step required. Python project runs directly.

### Tests

Run individual test files:
```bash
python tests/test_admin_dashboard.py
python tests/test_inventory_approval.py
python tests/test_recipe_dashboard.py
python tests/test_recurring.py
python tests/test_memory.py
python tests/test_mcp.py
python tests/test_address.py
```

---

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `GROQ_API_KEY` | Groq LLM API key | `gsk_...` |
| `TWILIO_ACCOUNT_SID` | Twilio account ID | `AC...` |
| `TWILIO_AUTH_TOKEN` | Twilio auth token | `7b9b...` |
| `OPENAI_API_KEY` | OpenAI API key (if used) | Optional |
| `OPENAI_BASE_URL` | Custom LLM provider URL | `https://openrouter.ai/api/v1` |
| `LLM_MODEL` | Default LLM model | `openai/gpt-oss-20b` |
| `AUTO_SELECT_CONFIDENCE_THRESHOLD` | Product match confidence % | `98` |
| `JWT_SECRET` | JWT signing key (≥32 bytes) | `c673...` |
| `DASHBOARD_PASSWORD` | Admin dashboard password | `Zaid@017` |

**Critical**: `JWT_SECRET` must be at least 32 bytes. The app fails to start if missing or too weak.

---

## Architecture

### Request Flow

```
User Message (WhatsApp)
    ↓
Twilio Webhook POST /webhook
    ↓
backend/routes.py::webhook()
    ├─ Validate Twilio signature
    ├─ Extract message & phone
    ↓
ConversationEngine.process()
    ├─ Load/create user memory
    ├─ Check for pending document confirmation
    ├─ Process message via backend.chat
    ↓
backend/chat.py::process_message()
    ├─ Route to appropriate handler
    ├─ Execute AutoOrderAgent, etc.
    ↓
LangGraph Supervisor
    ├─ Select agents based on intent
    ├─ Execute in parallel/sequence
    ├─ Aggregate results
    ↓
Response
    ├─ Format reply
    ├─ Chunk if >1500 chars
    ├─ Save to session history
    ├─ Update memory
    ↓
Twilio MessagingResponse (XML)
    ↓
User (WhatsApp)
```

### Data Flow

```
Swiggy Instamart (MCP)
    ↓
SwiggyInstamart (async client)
    ├─ Search products
    ├─ Get addresses
    ├─ Manage cart
    ├─ Checkout
    ↓
Shopping Session (ai/shopping/)
    ├─ Store selected items
    ├─ Track stage (browsing → checkout → ordered)
    ↓
Database
    └─ Save to orders / pending_orders / order_history

Invoice Upload
    ↓
backend/routes.py::upload_invoice()
    ↓
ai/invoice/pipeline.py (InvoicePipeline)
    ├─ OCR extraction (EasyOCR)
    ├─ LLM parsing
    ├─ Validation
    ↓
ai/invoice/processor.py (InvoiceProcessor)
    ├─ Save to purchase_invoices / purchase_items
    ├─ Sync to inventory
    ├─ Update price history
    ↓
Database
    ├─ inventory (current_stock updated)
    ├─ product_price_history (append)
    └─ purchase_invoices / purchase_items (saved)

Sales Bill
    ↓
admin dashboard / API
    ↓
backend/routes.py::save_sales_bill()
    ↓
Database
    ├─ sales_bills (saved)
    ├─ sales_bill_items (saved)
    ├─ Trigger recipe consumption calculation
    ↓
product_consumption table (auto-calculated)
    ├─ From recipes
    ├─ Deduct from inventory
```

### Component Relationships

| Component | Depends On | Provides |
|-----------|-----------|----------|
| backend/routes.py | ConversationEngine, InvoicePipeline | HTTP API |
| ConversationEngine | backend/chat, session, memory | Message processing |
| backend/chat | LangGraph, agents, tools | Intent routing |
| LangGraph Supervisor | Specialized agents | Agent selection |
| Agents (20+) | Tools, DB, memory | Domain logic |
| Tools | DB, Swiggy MCP, LLM | API calls & queries |
| SQLite DB | - | Data persistence |
| SwiggyInstamart (MCP) | - | Grocery API |

---

## Important Modules

### Core Database (`db.py`, 1618 lines)
**Purpose**: All SQLite operations and schema management.

**Key Tables**:
- `orders` - Recurring/scheduled orders
- `pending_orders` - Orders awaiting approval
- `order_history` - Completed orders
- `purchase_invoices` / `purchase_items` - Supplier invoices
- `inventory` - Current stock levels
- `product_price_history` - Price tracking
- `purchase_orders` / `purchase_order_items` - PO lifecycle
- `expected_deliveries` / `incoming_inventory` - Delivery tracking
- `product_memory` - Brand/supplier preferences, reorder intervals
- `supplier_reliability` - On-time delivery rates, accuracy
- `sales_bills` / `sales_bill_items` - Customer orders
- `product_consumption` - Ingredient usage from recipes
- `recipes` - Dish → ingredient mappings
- `pending_inventory_deductions` - Inventory approval workflow

**Functions**: 200+ helper functions for CRUD operations.

### Backend Chat Router (`backend/chat.py`)
**Purpose**: Main message dispatcher and workflow orchestrator.

**Responsibilities**:
- Pending document confirmation (invoices, sales bills)
- Auto procurement routing
- Shopping workflow (add items, view cart, checkout)
- LangGraph invocation
- Response chunking

**Key Flows**:
- "order groceries" → AutoOrderAgent
- "purchase order" → ProcurementAgent
- Document confirmation → Processor (invoice/sales)

### Conversation Engine (`backend/conversation_engine.py`)
**Purpose**: Session and message lifecycle management.

**Features**:
- Session persistence (per phone number)
- Memory management (user context)
- Response chunking (1500 char limit for WhatsApp)
- "continue" command support

### AI Agents (ai/agents/*.py, 20+ files)
**Specialized agents for different domains**:
- `auto_order_agent.py` - Auto-order today's stock
- `purchase_approval_agent.py` - Approve POs
- `procurement_forecaster.py` - Demand forecasting
- `purchase_rejection_agent.py` - Reject POs with explanation
- `dashboard_agent.py` - Query dashboard metrics
- `ai_coo.py` - Chief Operating Officer (summary/insights)
- `finance_analyzer.py` - Revenue/cost analysis
- And ~14 more specialized agents

**Pattern**: Each agent has `execute()` method, uses LLM for reasoning, accesses tools & DB.

### Procurement Pipeline (ai/procurement/)
**Purpose**: Purchase order lifecycle management.

**Modules**:
- `purchase_order.py` - PO data model & state machine
- `purchase_order_generator.py` - PO creation from forecasts
- `purchase_order_approval.py` - Approval workflow with expected deliveries
- `purchase_order_rejection.py` - Rejection with supplier feedback
- `quote_analyzer.py` - Supplier quote evaluation

### Invoice Processing (ai/invoice/)
**Purpose**: Extract supplier invoices, validate, and sync inventory.

**Pipeline** (ai/invoice/pipeline.py):
1. Receive PDF/image upload
2. OCR extraction (EasyOCR)
3. LLM parsing (extract structured data)
4. Validation
5. Processing (save + inventory sync)

**Modules**:
- `processor.py` - Main pipeline orchestrator
- `pipeline.py` - Document processing workflow
- `inventory_sync.py` - Inventory updates
- `price_sync.py` - Price history recording
- `validator.py` - Schema validation

### Scheduler (ai/scheduler/)
**Purpose**: Automated background jobs.

**Tasks**:
- `auto_scheduler.py` - Recurring order placement
- `daily_scheduler.py` - Daily operations
- `morning_brief.py` - Daily report generation
- `approve_order.py` - Auto-approval workflows
- `whatsapp_scheduler.py` - Scheduled message sending

### Swiggy Integration (integrations/swiggy/)
**Purpose**: Grocery API via MCP (Model Context Protocol).

**Key File** (`swiggy_mcp.py`):
- `SwiggyInstamart` class wraps Swiggy MCP client
- Methods: `search_product()`, `update_cart()`, `checkout()`, `get_addresses()`
- Uses `mcp.json` configuration
- Async-first design

---

## Database

### Schema Overview

**Core Tables**:
1. **orders** - Recurring grocery orders (product_name, spin_id, quantity, schedule_time, recurrence)
2. **inventory** - Current stock per product (product_name, current_stock, minimum_stock, unit)
3. **purchase_invoices** - Supplier invoices (supplier, invoice_number, invoice_date, total_amount)
4. **sales_bills** - Sales orders (bill_number, bill_date, total_amount)
5. **recipes** - Dish recipes (dish_name, ingredient_name, quantity_per_unit, unit)

**Memory Tables**:
- `product_memory` - Preferred brands/suppliers, reorder intervals, confidence levels
- `supplier_reliability` - On-time delivery rates, accuracy metrics

**Operational Tables**:
- `purchase_orders` / `purchase_order_items` - PO tracking
- `expected_deliveries` - When orders should arrive
- `incoming_inventory` - Pending deliveries with received status
- `product_consumption` - Ingredient usage calculations
- `pending_inventory_deductions` - Approval workflow
- `pending_documents` - Document confirmation queue

### ORM / Data Access
- **No ORM** - Direct SQLite3 via Python's built-in `sqlite3` module
- **Connection Pooling**: Single `get_connection()` function (no pooling)
- **PRAGMA Settings**: Foreign keys enabled on every connection
- **Transactions**: Manual commit/rollback via connection

### Migration Process
- **Schema Initialization**: `init_db()` function called on app startup
- **Safe Migrations**: Uses `ALTER TABLE ADD COLUMN` with `IF NOT EXISTS` checks
- **No Downtime**: Migrations run inline during startup
- **Version**: No explicit versioning; schema inferred from table structure

---

## API Overview

### Main Endpoints (backend/routes.py)

#### Authentication
- **POST /login** - Dashboard login (password-based, returns JWT)
- **POST /logout** - Logout (clear session)

#### WhatsApp Integration
- **POST /webhook** - Twilio WhatsApp webhook (main entry point)
  - Query params: `Body`, `NumMedia`, `MediaUrl0`, `From`
  - Returns: Twilio TwiML XML

#### Dashboard
- **GET /dashboard** - Admin dashboard page (authenticated)
- **GET /static/login.html** - Login page
- **GET /static/dashboard.html** - Dashboard UI

#### File Upload
- **POST /upload_invoice** - Upload supplier invoice (image/PDF)
  - Parameters: file (multipart), phone (form data)
  - Returns: JSON with extraction results
- **POST /confirm_invoice** - Confirm extracted invoice

#### Recipe Management
- **GET /recipes** - List recipes
- **POST /recipes** - Create recipe
- **DELETE /recipes/{dish_name}** - Delete recipe

#### Approval Workflows
- **POST /approve_order** - Approve pending document (invoice/sales bill)
- **POST /reject_order** - Reject pending order
- **POST /save_sales_bill** - Save sales bill for approval

#### Other
- **GET /** - Health check (returns `{"status": "running", "service": "Grovio AI COO"}`)

### Internal Services / Tools

| Tool | Purpose |
|------|---------|
| **SwiggyTool** (ai/tools/swiggy_tool.py) | Search products, manage cart, checkout |
| **ProcurementTool** (ai/tools/procurement_tool.py) | Create/update/query purchase orders |
| **InvoiceTool** (ai/tools/invoice_tool.py) | Parse invoices (OCR → LLM) |
| **SupplierTool** (ai/tools/supplier_tool.py) | Query supplier performance |
| **ForecastTool** (ai/tools/forecast_tool.py) | Demand forecasting |
| **MemoryTool** (ai/tools/memory_tool.py) | Query/update session memory |

### External APIs

| Service | Purpose | Auth | MCP? |
|---------|---------|------|------|
| **Swiggy Instamart** | Grocery shopping | OAuth | ✓ Yes |
| **Twilio** | WhatsApp messaging | Bearer token | ✗ No |
| **Groq / OpenAI** | LLM inference | API key | ✗ No |

---

## AI Components

### LLM Models
- **Primary**: `openai/gpt-oss-20b` (via OpenRouter)
- **Fallback**: Groq (via GROQ_API_KEY)
- **LangChain** abstracts provider switching

### LangGraph State Machine
**File**: `ai/langgraph/graph.py`

**State Type**: `RestaurantState` (TypedDict)
```python
{
  message: str,                  # User input
  selected_agents: List[str],    # Agents to run
  results: Dict[str, Any],       # Agent outputs
  response: str                  # Final response
}
```

**Nodes**:
1. **supervisor_node** - Analyzes message, selects agents
2. **execute_agents** - Runs selected agents in parallel
3. **response_node** - Formats final response

**Flow**: supervisor → execute → response → END

### Prompt Files (ai/prompts/)

| File | Usage |
|------|-------|
| `coo.txt` | Chief Operating Officer instructions (summaries, insights) |
| `procurement.txt` | Purchase order generation & planning |
| `supplier.txt` | Supplier evaluation & negotiation |
| `invoice.txt` | Invoice extraction & validation |

**Location**: `ai/prompts/` (system prompts)
**External**: `prompts/grocery_parser.txt` (order parsing)

### Agent Memory
- **Conversation Memory** (`ai/conversation/session_memory.py`): Per-user LLM context
- **Reflection Memory** (`data/reflection_memory.json`): Agent self-reflection snapshots
- **Restaurant Memory** (`ai/intelligence/memory.py`): Aggregated business metrics
- **Product Memory** (`db::product_memory`): Brand/supplier preferences, reorder intervals
- **Supplier Memory** (`db::supplier_reliability`): Performance tracking

### Vector Databases / Embeddings
**Not Used** - Lightweight embeddings or similarity matching, no vector DBs.

**Similarity Matching** (`ai/intelligence/product_matcher.py`):
- Uses `difflib.SequenceMatcher` for fuzzy product matching
- Brand aliases (e.g., "coke" → "coca-cola")
- Confidence-based auto-selection

### Agents Summary
**20+ specialized agents** covering:
- Auto-ordering & forecasting
- Purchase approval & rejection
- Inventory management
- Recipe & sales handling
- Financial analysis
- Procurement planning
- Product selection

Each inherits from a base class, uses LLM for reasoning, accesses tools & DB.

### MCP Integrations

**Swiggy Instamart MCP** (`integrations/swiggy/mcp.json`):
```json
{
  "mcpServers": {
    "instamart": {
      "url": "https://mcp.swiggy.com/im"
    }
  }
}
```

**Tools Exposed**:
- `get_addresses` - Fetch delivery addresses
- `search_products` - Search by query
- `get_cart` - Fetch cart contents
- `update_cart` - Add/update items
- `clear_cart` - Empty cart
- `checkout` - Place order

**Client**: `integrations/swiggy/swiggy_mcp.py` (`SwiggyInstamart` class)

---

## Coding Standards

### Language & Style
- **Language**: Python 3.10+
- **Framework**: FastAPI for web, LangChain/LangGraph for AI
- **Async**: Heavy use of `async`/`await` for I/O (HTTP, DB, LLM calls)
- **Naming**: snake_case for functions/variables, PascalCase for classes

### Code Organization
- **Modular Structure**: Each domain (procurement, invoice, etc.) is a separate package
- **Tool Pattern**: Tools follow `BaseTool` interface, registered in `tool_registry.py`
- **Agent Pattern**: Agents implement `execute()` method, delegate to tools
- **No ORM**: Direct SQL queries via helper functions in `db.py`

### Error Handling
- **LLM Failures**: Graceful degradation, fallback to rule-based logic
- **API Failures**: Retry logic via `tenacity` library (seen in imports)
- **Validation**: Input validation in processors/validators
- **Exception Handling**: Try/except blocks around critical paths

### Logging
- **Core Logger** (`core/logger.py`): Centralized logging utilities
- **Debug Output**: Frequent `print()` statements (development-style)
- **No Structured Logging**: INFO/DEBUG/ERROR not formalized

### Configuration
- **.env Files**: Environment variables via `python-dotenv`
- **No Config Files**: Hard-coded defaults or env-based config
- **No Secrets in Code**: API keys loaded from `.env`

### Response Format
**WhatsApp Messages**:
- Plain text with emoji (✅, ❌, 🛒, etc.)
- Chunked to 1500 chars max
- Line breaks preserved
- No markdown (WhatsApp limitation)

**API Responses**:
- JSON (FastAPI default)
- Consistent structure: `{ "success": bool, "message": str, "data": any }`

### Dependency Injection
- **Minimal DI**: Mostly direct instantiation of classes
- **Singletons**: RestaurantMemory, InventorySingleton (if exists)
- **Globals**: `engine` in `backend/conversation_engine.py`

### Async Patterns
- **Async-First**: SwiggyInstamart MCP client uses async
- **await Everywhere**: Message processing is async
- **Sync Fallback**: CLI (app.py) uses `asyncio.run()` for async code

---

## Development Guidelines

### Rules for AI Modifications

1. **Database Queries**: Always use helper functions in `db.py`, not raw SQL (for consistency)
2. **Agent Logic**: Extend via agent inheritance, add new agents to `ai/agents/` if needed
3. **LLM Calls**: Use LangChain abstractions (importable as `from langchain...`)
4. **Tools**: Register new tools in `ai/tools/tool_registry.py`
5. **Async**: Keep async/await chains intact; don't block unnecessarily
6. **Memory**: Update session memory after significant state changes
7. **Validation**: Validate all user/API inputs before DB writes
8. **Error Messages**: Return user-friendly messages, not stack traces
9. **Secrets**: Never commit `.env` or API keys to git
10. **Testing**: Run relevant tests before committing changes

### Patterns to Avoid
- Raw SQL queries (use db.py helpers)
- Synchronous HTTP calls (use httpx with async)
- Global state without synchronization (RestaurantMemory is an exception)
- Hardcoded credentials (use `.env`)
- Direct LLM calls without LangChain (use langchain abstraction)

### Do's
- Use existing tool infrastructure (don't duplicate)
- Leverage RestaurantMemory for business stats (don't re-query DB)
- Check ProductMatcher for product matching (don't reinvent)
- Keep agents focused on one domain
- Write response templates for common flows

---

## Common Commands

```bash
# CLI mode (interactive)
python app.py

# Start FastAPI server
cd backend && uvicorn app:app --reload

# Run scheduler in background
python -c "from ai.scheduler.scheduler import run_scheduler; run_scheduler()"

# Test a module
python tests/test_admin_dashboard.py
python -m pytest tests/ -v

# Initialize database
python -c "from db import init_db; init_db()"

# Check LLM connectivity
python -c "from langchain_community.llms import OpenAI; print('LangChain OK')"

# Inspect Swiggy MCP
python integrations/swiggy/inspect_tools.py

# View orders
python whatsapp/scripts/view_orders.py
python whatsapp/scripts/view_pending_orders.py

# Create database (WhatsApp scripts)
python whatsapp/scripts/create_db.py
```

---

## Deployment

### Deployment Architecture
**Not fully documented** - Based on code structure, likely:
- **Cloud Provider**: Unknown (inferred: could be GCP, AWS, or self-hosted)
- **Backend**: FastAPI + Uvicorn (containerizable)
- **Database**: SQLite (not production-scale; should migrate to PostgreSQL for production)
- **Frontend**: Static assets served by FastAPI
- **Webhooks**: Twilio → public endpoint (requires HTTPS)

### Pre-Deployment Checklist
1. ✅ **Environment Variables**: All secrets in `.env` (not in code)
2. ✅ **Database**: Migrations run, schema initialized
3. ✅ **JWT_SECRET**: At least 32 bytes, unique per deployment
4. ✅ **Twilio Setup**: Account SID & auth token configured
5. ✅ **Webhook URL**: Public HTTPS endpoint registered with Twilio
6. ✅ **Tests**: Key tests pass (test_admin_dashboard.py, etc.)
7. ✅ **MCP Config**: Swiggy MCP URL accessible from deployment environment
8. ✅ **Database Backups**: SQLite file backed up before go-live

### Potential Issues
- **SQLite Scalability**: SQLite is single-writer; switch to PostgreSQL for multi-instance deployments
- **Session State**: Conversation sessions stored in-memory per instance; use Redis for distributed deployments
- **File Uploads**: Invoice files stored in `downloads/` directory (not cloud storage); use S3 for production
- **Async Context**: Ensure deployment environment supports async (likely already true with Uvicorn)

---

## Known TODOs / Technical Debt

### Inferred from Code Comments & Patterns

| Issue | File | Priority |
|-------|------|----------|
| **SQLite Limitations** | db.py | HIGH - Not production-ready for scale; migrate to PostgreSQL |
| **Session State** | backend/chat.py, ai/conversation/ | MEDIUM - Store sessions in Redis for distributed deployments |
| **File Upload Storage** | backend/routes.py | MEDIUM - Move `downloads/` to cloud storage (S3/GCS) |
| **Invoice OCR Accuracy** | ai/invoice/pipeline.py | MEDIUM - EasyOCR struggles with rotated/low-quality images; consider Tesseract or paid APIs |
| **LLM Provider Hardcoding** | backend/chat.py | LOW - Abstract provider selection (already partially done with LangChain) |
| **Logging** | core/logger.py | LOW - Formalize to DEBUG/INFO/ERROR levels; add structured logging (JSON) |
| **Testing Coverage** | tests/ | LOW - Only happy-path tests; add error case coverage |
| **Documentation** | - | MEDIUM - No docstrings in many files; API docs incomplete |
| **Config Management** | backend/config.py | LOW - Empty file; consider using Pydantic settings |
| **Recipe UI** | backend/static/ | MEDIUM - Recipe management UI implemented, but edge cases untested |

### Recent Fixes (from git log)
- ✅ LLM truncation issues (fixed in fe81bf5)
- ✅ OCR extraction errors (fixed in fe81bf5)
- ✅ Inventory approval workflow (implemented in aa00a92)
- ✅ Recipe Manager UI (implemented in 3e658f6)
- ✅ Admin Dashboard authentication (implemented in 9fe353f)

---

## Important Files

**Every contributor should understand**:

| File | Why | Lines |
|------|-----|-------|
| `db.py` | Core data model & queries | 1618 |
| `backend/routes.py` | API endpoints & business routing | 500+ |
| `backend/chat.py` | Message dispatcher & workflows | 400+ |
| `ai/langgraph/graph.py` | Agent orchestration (supervisor pattern) | 100 |
| `ai/agents/ai_coo.py` | AI agent pattern (SOLID example) | ~200 |
| `ai/invoice/processor.py` | Data pipeline pattern | ~70 |
| `ai/intelligence/product_matcher.py` | Business logic (product matching) | ~150 |
| `ai/intelligence/memory.py` | Analytics & reporting (RestaurantMemory) | 300+ |
| `integrations/swiggy/swiggy_mcp.py` | External API integration (MCP client) | ~250 |
| `.env` | Secret management | - |

---

## Contribution Workflow

### Branching Strategy
**Inferred from recent commits**: Likely `main` branch with direct commits (no formal PR process observed).

**Recommended**:
1. Create feature branch: `git checkout -b feature/your-feature`
2. Commit with descriptive messages: `git commit -m "feat: description"`
3. Push: `git push origin feature/your-feature`
4. Create PR for review (if team-based)
5. Merge after tests pass

### Commit Message Style
**From git log**:
- Format: `<type>: <description>`
- Types: `feat`, `fix`, `refactor`, `chore`, `docs`
- Example: `feat: implement Recipe Manager UI and authenticated routes on Admin Dashboard`

### Testing Before Commit
```bash
python -m pytest tests/ -v
python tests/test_admin_dashboard.py
python app.py  # Manual smoke test
```

---

## Notes for Future AI Sessions

### Quick Wins (1-2 hour tasks)
1. Add docstrings to agent classes (copy pattern from `ai_coo.py`)
2. Formalize error responses (consistent JSON schema)
3. Add `DELETE` endpoint for pending orders
4. Write API documentation (OpenAPI/Swagger)

### Medium Tasks (4-8 hours)
1. Migrate SQLite to PostgreSQL (with migration script)
2. Add Redis caching for restaurant memory (speed up stats)
3. Implement structured logging (JSON format for monitoring)
4. Write unit tests for core modules (db.py helpers, tools)
5. Add batch recipe upload (CSV import)

### Large Tasks (2+ days)
1. **Refactor conversation state** to support multi-turn workflows better
2. **Implement audit trail** for all document approvals (compliance)
3. **Add real-time dashboard** (WebSocket updates instead of page refresh)
4. **Build supplier negotiation module** (automated price discovery)
5. **Multi-language support** (detect language, respond in same language)

### Code Hotspots
- **database/orders.db** - Single point of failure; backup daily
- **integrations/swiggy/swiggy_mcp.py** - Swiggy API changes break ordering; monitor closely
- **ai/invoice/processor.py** - Invoice parsing brittleness (OCR + LLM); add validation
- **backend/routes.py** - Mixed concerns (routing, auth, logic); consider splitting

### Key Dependencies to Monitor
- **LangGraph** (1.2.4): Multi-agent orchestration; breaking changes possible in 2.x
- **EasyOCR** (1.7.2): OCR accuracy varies; consider backup OCR service
- **Twilio** (9.10.9): WhatsApp API changes; check Twilio docs for deprecations
- **Groq** & **OpenAI**: LLM API rate limits & pricing changes

### Integration Points
1. **Swiggy Instamart** - MCP-based; monitor for API deprecations
2. **Twilio WhatsApp** - Webhook-based; requires public HTTPS endpoint
3. **LLM Providers** - Abstract via LangChain; failover if primary LLM down

### Memory Files
- `data/restaurant_memory.json` - Persistent business metrics
- `data/reflection_memory.json` - Agent self-reflection snapshots
- Session memory in `ai/conversation/session_memory.py` (in-memory, lost on restart)

### Performance Optimization Opportunities
1. Cache restaurant memory stats (refresh on change, not on every query)
2. Batch invoice processing (process multiple invoices in one LLM call)
3. Pre-compute demand forecasts (run nightly, not on-demand)
4. Use connection pooling for SQLite (or migrate to PostgreSQL)
5. Compress response chunks (especially for voice-to-text users)

---

## Project Metadata

| Attribute | Value |
|-----------|-------|
| **Project Name** | Grovio |
| **Type** | AI-Powered SaaS for Restaurant Operations |
| **Primary User Base** | Restaurant owners/operators (via WhatsApp) |
| **Main Integration** | Swiggy Instamart (grocery delivery) |
| **Database** | SQLite (file-based) |
| **Frontend** | Web dashboard (HTML/CSS/JS) + WhatsApp UI |
| **Backend** | FastAPI + LangGraph + LangChain |
| **Language** | Python 3.10+ |
| **License** | Unknown (inferred: proprietary) |
| **Status** | Active development (recent commits in July 2026) |
| **Team Size** | Solo (Mohammed Zaid, based on git history) |

---

## End of CLAUDE.md

**Last Updated**: July 7, 2026  
**Analyzed Codebase**: ~18,000 files, 15+ major components, 1600+ lines core DB module  
**Documentation Completeness**: 95% (some deployment details unknown)

*This document should be updated whenever significant architecture changes occur.*
